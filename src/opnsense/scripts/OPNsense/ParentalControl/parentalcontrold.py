#!/usr/local/bin/python3
"""
parentalcontrold - ouderlijk toezicht voor OPNsense.

  * bedtijden: per groep een pf-table in het anchor 'parentalcontrol'; een vaste
    regel blokkeert verkeer van adressen in die table
  * tijdtelling: per tick wordt per groep EEN keer de verstreken tijd opgeteld als
    minstens een apparaat van de groep actief was (gelijktijdig gebruik telt dus
    niet dubbel). Activiteit = bytes-delta over de pf-states van de apparaten.
  * dagbudgetten: is het budget (plus extra tijd) van een groep op, dan wordt de groep
    op dezelfde manier geblokkeerd als bij een bedtijd
  * extra tijd: de GUI zet een commando in een wachtrij (QUEUE_DIR); de daemon verwerkt
    die direct en telt de minuten bij de extra tijd van vandaag
  * blokkeerpagina: geblokkeerde apparaten krijgen DNS en HTTP (poort 80) omgeleid naar een
    lokale webserver (pc_web) met uitleg en, als de tijd op is, de mogelijkheid om extra tijd
    te vragen (maximaal een verzoek per cooldown-periode). De ouder beslist in de GUI.
"""
import json
import os
import re
import secrets
import signal
import sys
import syslog
import threading
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
import ipaddress

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pc_adguard  # noqa: E402
import pc_web  # noqa: E402
from pc_common import fmt_dur, norm_ip, norm_mac, neighbours, run  # noqa: E402
from pc_notify import notify  # noqa: E402

CONFIG_XML = '/conf/config.xml'
USAGE_FILE = '/var/db/parentalcontrol/usage.json'
STATUS_FILE = '/var/run/parentalcontrol/status.json'
QUEUE_DIR = '/var/db/parentalcontrol/queue'
REQUESTS_FILE = '/var/db/parentalcontrol/requests.json'
REQUEST_OPTIONS = (15, 30, 60)   # minuten die een kind kan vragen
REQUEST_EXPIRY = 3600            # onbeantwoorde verzoeken verlopen na een uur
# Bekende openbare DNS-resolvers (voor het blokkeren van DoH/DoT); niet uitputtend.
DOH_IPS = ['8.8.8.8', '8.8.4.4', '1.1.1.1', '1.0.0.1', '9.9.9.9', '149.112.112.112',
           '208.67.222.222', '208.67.220.220', '94.140.14.14', '94.140.15.15',
           '2001:4860:4860::8888', '2001:4860:4860::8844', '2606:4700:4700::1111',
           '2606:4700:4700::1001', '2620:fe::fe', '2620:fe::9', '2620:119:35::35',
           '2620:119:53::53', '2a10:50c0::ad1:ff', '2a10:50c0::ad2:ff']
PLAN_TTL = 300                   # seconden dat de DNS-plan (interfaces/subnetten) gecached blijft
WEEKDAYS_NL = ['maandag', 'dinsdag', 'woensdag', 'donderdag', 'vrijdag', 'zaterdag', 'zondag']
ANCHOR = 'parentalcontrol'
PFCTL = '/sbin/pfctl'
DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']  # datetime.weekday() volgorde
USAGE_SAVE_INTERVAL = 60
USAGE_KEEP_DAYS = 35


def log(msg, level=syslog.LOG_NOTICE):
    syslog.syslog(level, msg)


def atomic_write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w') as handle:
        json.dump(data, handle)
    os.replace(tmp, path)


# ---------------------------------------------------------------- bedtijden

def parse_hhmm(value):
    try:
        h, m = (value or '').split(':')
        return int(h) * 60 + int(m)
    except ValueError:
        return None


def window_active(win, now):
    """
    win: {'days': set, 'start': minuten, 'end': minuten}
    Een bedtijd hoort bij de dag waarop hij begint. Is end < start, dan loopt hij
    door tot de volgende ochtend. start == end wordt genegeerd.
    """
    s, e = win['start'], win['end']
    if s is None or e is None or s == e:
        return False
    minute = now.hour * 60 + now.minute
    today = DAYS[now.weekday()] in win['days']
    yesterday = DAYS[(now.weekday() - 1) % 7] in win['days']
    if s < e:
        return today and s <= minute < e
    return (today and minute >= s) or (yesterday and minute < e)


def bedtime_until(bedtimes, uuid, now):
    """Tekst als 'ma 07:00' voor het eerstvolgende einde van de bedtijd (max 48 uur vooruit)."""
    wins = [w for w in bedtimes if w['enabled'] and uuid in w['groups']]
    t = now.replace(second=0, microsecond=0)
    for _ in range(48 * 60):
        t += timedelta(minutes=1)
        if not any(window_active(w, t) for w in wins):
            if t.date() == now.date():
                return t.strftime('%H:%M')
            if t.date() == now.date() + timedelta(days=1):
                return 'morgen %s' % t.strftime('%H:%M')
            return '%s %s' % (WEEKDAYS_NL[t.weekday()], t.strftime('%H:%M'))
    return None


def daily_limit(budgets, uuid, now):
    """Laagste passende dagbudget in seconden voor deze groep, of None (onbeperkt)."""
    day = DAYS[now.weekday()]
    limits = [b['minutes'] * 60 for b in budgets
              if b['enabled'] and uuid in b['groups'] and day in b['days'] and b['minutes'] is not None]
    return min(limits) if limits else None


def table_name(uuid):
    return 'pc_' + uuid.replace('-', '')[:12]


def pfctl(args, stdin=None):
    return run([PFCTL, '-a', ANCHOR] + args, stdin)


def table_show(name):
    out = pfctl(['-t', name, '-T', 'show']).stdout
    return {ip for ip in (norm_ip(x) for x in out.split()) if ip}


def parse_ifconfig(text):
    """-> {interface: [ip_network, ...]} (zonder link-local adressen)."""
    nets, cur = {}, None
    for line in text.splitlines():
        m = re.match(r'^(\S+): flags=', line)
        if m:
            cur = m.group(1)
            nets.setdefault(cur, [])
            continue
        if cur is None:
            continue
        m = re.match(r'\s+inet (\S+) netmask (0x[0-9a-fA-F]+)', line)
        if m:
            try:
                prefix = bin(int(m.group(2), 16)).count('1')
                nets[cur].append(ipaddress.ip_network('%s/%d' % (m.group(1), prefix), strict=False))
            except ValueError:
                pass
            continue
        m = re.match(r'\s+inet6 (\S+?)(?:%\S+)? prefixlen (\d+)', line)
        if m:
            try:
                addr = ipaddress.ip_address(m.group(1))
                if not addr.is_link_local:
                    nets[cur].append(ipaddress.ip_network('%s/%s' % (m.group(1), m.group(2)), strict=False))
            except ValueError:
                pass
    return nets


def registered_anchors():
    """Welke soorten anchors 'parentalcontrol' staan in de actieve hoofdregels: {'rdr','nat','fw'}."""
    found = set()
    translation = run([PFCTL, '-sn']).stdout
    if 'rdr-anchor "%s"' % ANCHOR in translation:
        found.add('rdr')
    if 'nat-anchor "%s"' % ANCHOR in translation:
        found.add('nat')
    if re.search(r'^anchor "%s"' % ANCHOR, run([PFCTL, '-sr']).stdout, re.M):
        found.add('fw')
    return found


def build_dns_lines(plan):
    """pf-regels voor DNS-afdwinging -> (tabellen, nat, rdr, filter)."""
    servers = plan['servers']
    s4 = [x for x in servers if ':' not in x]
    s6 = [x for x in servers if ':' in x]
    ifs = '{ %s }' % ' '.join(plan['ifs'])
    tables = ['table <pc_dns_servers> const { %s }' % ' '.join(servers)]
    nat, rdr, filt = [], [], []
    group_block = bool(plan.get('group_dns_block'))
    if group_block:
        tables.append('table <pc_pdev> persist')
    for ifname, net, family in plan['nat']:
        nat.append('nat on %s %s proto { tcp udp } from %s to <pc_dns_servers> port 53 -> (%s)' %
                   (ifname, 'inet6' if family == 6 else 'inet', net, ifname))
    rdr.append('no rdr on %s proto { tcp udp } from any to <pc_dns_servers> port 53' % ifs)
    if group_block:
        # apparaten met een profiel: eigen DNS-server wordt geblokkeerd in plaats van omgeleid,
        # zodat AdGuard het apparaat blijft herkennen en het profiel niet te omzeilen is
        rdr.append('no rdr on %s proto { tcp udp } from <pc_pdev> to any port 53' % ifs)
        filt.append('block return in quick on %s proto { tcp udp } from <pc_pdev> to ! <pc_dns_servers> port 53'
                    % ifs)
    if s4:
        rdr.append('rdr pass on %s inet proto { tcp udp } from ! <pc_dns_servers> to any port 53 -> %s port 53'
                   % (ifs, s4[0]))
    if s6:
        rdr.append('rdr pass on %s inet6 proto { tcp udp } from ! <pc_dns_servers> to any port 53 -> %s port 53'
                   % (ifs, s6[0]))
    else:
        filt.append('block return in quick on %s inet6 proto { tcp udp } from ! <pc_dns_servers> to any port 53'
                    % ifs)
    if not s4:
        filt.append('block return in quick on %s inet proto { tcp udp } from ! <pc_dns_servers> to any port 53'
                    % ifs)
    if plan['block_encrypted']:
        tables.append('table <pc_doh> const { %s }' % ' '.join(DOH_IPS))
        filt.append('block return in quick on %s proto tcp from ! <pc_dns_servers> to <pc_doh> port { 443 853 }' % ifs)
        filt.append('block return in quick on %s proto { tcp udp } from ! <pc_dns_servers> to any port 853' % ifs)
    return tables, nat, rdr, filt


def build_ruleset(groups, page_port=None, dns_servers=(), plan=None):
    """
    Met page_port: HTTP (poort 80) van geblokkeerde apparaten wordt omgeleid naar de
    blokkeerpagina en de rest geweigerd ('return': snelle foutmelding). DNS van geblokkeerde
    apparaten: met dns_servers alleen naar die servers (bijv. AdGuard), anders omgeleid naar
    Unbound op deze firewall. Zonder page_port wordt alles stil genegeerd ('drop').
    plan: DNS-afdwinging voor alle clients op de gekozen interfaces (zie Daemon.dns_plan).
    """
    tables = ['table <%s> persist' % g['table'] for g in groups]
    nat, rdr, filt, passes = [], [], [], []
    if plan:
        t, nat, rdr, filt = build_dns_lines(plan)
        tables = t + tables
    if page_port:
        for g in groups:
            t = g['table']
            if dns_servers:
                passes.append('pass in quick proto { tcp udp } from <%s> to { %s } port 53' %
                              (t, ' '.join(dns_servers)))
            else:
                rdr += [
                    'rdr pass inet proto { tcp udp } from <%s> to any port 53 -> 127.0.0.1 port 53' % t,
                    'rdr pass inet6 proto { tcp udp } from <%s> to any port 53 -> ::1 port 53' % t,
                ]
            rdr += [
                'rdr pass inet proto tcp from <%s> to any port 80 -> 127.0.0.1 port %d' % (t, page_port),
                'rdr pass inet6 proto tcp from <%s> to any port 80 -> ::1 port %d' % (t, page_port),
            ]
    action = 'return' if page_port else 'drop'
    blocks = ['block %s in quick from <%s> to any' % (action, g['table']) for g in groups]
    return '\n'.join(tables + nat + rdr + filt + passes + blocks) + '\n'


# ---------------------------------------------------------------- verkeer meten

def extract_ips(line):
    """Haal alle IP-adressen uit de kopregel van een pf-state."""
    ips = set()
    for tok in re.split(r'[\s()]+', line):
        tok = re.sub(r'\[\d+\]$', '', tok)          # IPv6: adres[poort]
        if tok.count(':') == 1 and '.' in tok:       # IPv4: adres:poort
            tok = tok.split(':')[0]
        ip = norm_ip(tok) if tok and (tok.count(':') >= 2 or tok.replace('.', '').isdigit()) else None
        if ip:
            ips.add(ip)
    return ips


def parse_states(output):
    """Parse 'pfctl -ss -vv' naar [{'ips', 'bytes', 'age', 'key'}]."""
    states, cur = [], None
    for line in output.splitlines():
        if not line.strip():
            continue
        if not line[0].isspace():
            if cur:
                states.append(cur)
            cur = {'ips': extract_ips(line), 'bytes': 0, 'age': 0, 'key': None}
            continue
        if cur is None:
            continue
        m = re.search(r'age (\d+):(\d+):(\d+)', line)
        if m:
            cur['age'] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        m = re.search(r'(\d+):(\d+) bytes', line)
        if m:
            cur['bytes'] = int(m.group(1)) + int(m.group(2))
        m = re.search(r'id: ([0-9a-fA-F]+) creatorid: ([0-9a-fA-F]+)', line)
        if m:
            cur['key'] = m.group(1) + m.group(2)
    if cur:
        states.append(cur)
    return states


class TrafficMeter:
    """Berekent per groep het aantal bytes sinds de vorige meting."""

    def __init__(self):
        self.seen = {}

    def update(self, states, ip_group, interval):
        new_seen, per_group = {}, {}
        for st in states:
            if not st['key']:
                continue
            groups = {g for ip in st['ips'] for g in ip_group.get(ip, ())}
            if not groups:
                continue
            total = st['bytes']
            new_seen[st['key']] = total
            prev = self.seen.get(st['key'])
            if prev is None:
                # nieuwe state: telt volledig mee, tenzij hij al lang bestond
                # (bijv. bij het starten van de daemon): dan alleen basislijn
                delta = total if st['age'] <= 2 * interval + 5 else 0
            else:
                delta = max(0, total - prev)
            for g in groups:
                per_group[g] = per_group.get(g, 0) + delta
        self.seen = new_seen
        return per_group


class GroupTimer:
    """
    Telt actieve tijd van een groep. Inactieve tijd direct na activiteit
    ('naloop') telt pas mee als er binnen de naloop opnieuw activiteit is;
    daarna wordt hij weggegooid. Zo telt een sessie niet te lang door.
    """

    def __init__(self):
        self.idle = 0.0
        self.in_session = False

    def update(self, active, dt, grace):
        if active:
            add = dt + (self.idle if self.in_session else 0.0)
            self.idle = 0.0
            self.in_session = True
            return add
        if self.in_session:
            self.idle += dt
            if self.idle > grace:
                self.in_session = False
                self.idle = 0.0
        return 0.0

    def state(self, active):
        if active:
            return 'active'
        return 'idle' if self.in_session else 'inactive'


class UsageStore:
    """Verbruik en extra tijd per dag per groep (seconden)."""

    def __init__(self, path):
        self.path = path
        self.days = {}
        self.extra = {}
        self.dirty = False
        try:
            with open(path) as handle:
                data = json.load(handle)
            self.days = data.get('days', {})
            self.extra = data.get('extra', {})
        except (OSError, ValueError):
            pass

    def add(self, day, uuid, secs):
        entry = self.days.setdefault(day, {})
        entry[uuid] = round(entry.get(uuid, 0.0) + secs, 1)
        self.dirty = True

    def get(self, day, uuid):
        return self.days.get(day, {}).get(uuid, 0.0)

    def add_extra(self, day, uuid, secs):
        entry = self.extra.setdefault(day, {})
        entry[uuid] = entry.get(uuid, 0.0) + secs
        self.dirty = True

    def get_extra(self, day, uuid):
        return self.extra.get(day, {}).get(uuid, 0.0)

    def save(self):
        cutoff = (date.today() - timedelta(days=USAGE_KEEP_DAYS)).isoformat()
        self.days = {k: v for k, v in self.days.items() if k >= cutoff}
        self.extra = {k: v for k, v in self.extra.items() if k >= cutoff}
        try:
            atomic_write(self.path, {'days': self.days, 'extra': self.extra})
            self.dirty = False
        except OSError as err:
            log('verbruik opslaan mislukt: %s' % err, syslog.LOG_ERR)


class RequestStore:
    """Verzoeken om extra tijd (pending/approved/denied/expired)."""

    def __init__(self, path):
        self.path = path
        self.items = []
        try:
            with open(path) as handle:
                self.items = json.load(handle).get('requests', [])
        except (OSError, ValueError):
            pass

    def save(self):
        self.items = sorted(self.items, key=lambda r: r['created'])[-50:]
        try:
            atomic_write(self.path, {'requests': self.items})
        except OSError as err:
            log('verzoeken opslaan mislukt: %s' % err, syslog.LOG_ERR)

    def add(self, uuid, name, minutes):
        req = {'id': secrets.token_hex(6), 'group': uuid, 'name': name, 'minutes': minutes,
               'created': time.time(), 'status': 'pending', 'resolved': None}
        self.items.append(req)
        self.save()
        return req

    def get(self, rid):
        return next((r for r in self.items if r['id'] == rid), None)

    def latest(self, uuid):
        mine = [r for r in self.items if r['group'] == uuid]
        return max(mine, key=lambda r: r['created']) if mine else None

    def resolve(self, rid, approve, ts):
        req = self.get(rid)
        if not req or req['status'] != 'pending':
            return False
        req['status'] = 'approved' if approve else 'denied'
        req['resolved'] = ts
        self.save()
        return True

    def expire(self, ts):
        changed = False
        for req in self.items:
            if req['status'] == 'pending' and ts - req['created'] > REQUEST_EXPIRY:
                req['status'] = 'expired'
                req['resolved'] = ts
                changed = True
        if changed:
            self.save()


# ---------------------------------------------------------------- config

EMPTY_CFG = {'enabled': False, 'interval': 10, 'threshold_kb': 60, 'grace_min': 3,
             'blockpage': True, 'page_port': 8089, 'dns_servers': [], 'dns_enforce': False, 'dns_block_encrypted': True,
             'dns_ifnames': [], 'request_enabled': True, 'request_cooldown': 15,
             'filter_backend': 'none', 'adguard_url': '', 'adguard_user': '', 'adguard_password': '',
             'adguard_verify_tls': True, 'dns_group_mode': 'block', 'profiles': {},
             'notify': {}, 'groups': {}, 'devices': [], 'bedtimes': [], 'budgets': []}


def parse_config(root):
    node = root.find('OPNsense/ParentalControl')
    cfg = dict(EMPTY_CFG, groups={}, devices=[], bedtimes=[], budgets=[], notify={}, profiles={})
    if node is None:
        return cfg
    cfg['enabled'] = node.findtext('general/enabled') == '1'

    def num(path, default, lo, hi):
        try:
            return max(lo, min(hi, int(node.findtext(path) or default)))
        except ValueError:
            return default

    cfg['interval'] = num('general/tick_interval', 10, 5, 60)
    cfg['threshold_kb'] = num('general/activity_threshold', 60, 1, 100000)
    cfg['grace_min'] = num('general/idle_grace', 3, 0, 60)
    cfg['blockpage'] = node.findtext('general/blockpage', '1') == '1'
    cfg['page_port'] = num('general/page_port', 8089, 1024, 65535)
    cfg['dns_servers'] = []
    for item in (node.findtext('general/dns_servers') or '').split(','):
        ip = norm_ip(item)
        if ip and ip not in cfg['dns_servers']:
            cfg['dns_servers'].append(ip)
    cfg['dns_enforce'] = node.findtext('general/dns_enforce') == '1'
    cfg['dns_block_encrypted'] = node.findtext('general/dns_block_encrypted', '1') == '1'
    cfg['dns_ifnames'] = []
    for ident in (node.findtext('general/dns_interfaces') or '').split(','):
        ifname = root.findtext('interfaces/%s/if' % ident.strip()) if ident.strip() else None
        if ifname and re.fullmatch(r'[A-Za-z0-9_.]+', ifname) and ifname not in cfg['dns_ifnames']:
            cfg['dns_ifnames'].append(ifname)
    cfg['filter_backend'] = node.findtext('general/filter_backend') or 'none'
    cfg['adguard_url'] = (node.findtext('general/adguard_url') or '').strip()
    cfg['adguard_user'] = node.findtext('general/adguard_user') or ''
    cfg['adguard_password'] = node.findtext('general/adguard_password') or ''
    cfg['adguard_verify_tls'] = node.findtext('general/adguard_verify_tls', '1') == '1'
    cfg['dns_group_mode'] = node.findtext('general/dns_group_mode') or 'block'
    cfg['request_enabled'] = node.findtext('general/request_enabled', '1') == '1'
    cfg['request_cooldown'] = num('general/request_cooldown', 15, 1, 1440)
    cfg['notify'] = {k: (node.findtext('general/' + k) or '').strip() for k in
                     ('ntfy_server', 'ntfy_topic', 'ntfy_token', 'webhook_url', 'gui_url')}
    for g in node.findall('groups/group'):
        uuid = g.get('uuid')
        cfg['groups'][uuid] = {
            'uuid': uuid,
            'name': g.findtext('name') or uuid,
            'enabled': g.findtext('enabled') == '1',
            'table': table_name(uuid),
            'profile': g.findtext('profile') or '',
        }
    def domains(text):
        return pc_adguard.clean_domains((text or '').split(','))

    for pr in node.findall('profiles/profile'):
        cfg['profiles'][pr.get('uuid')] = {
            'name': pr.findtext('name') or '',
            'safesearch': pr.findtext('safesearch') == '1',
            'parental': pr.findtext('parental') == '1',
            'categories': set(filter(None, (pr.findtext('categories') or '').split(','))),
            'blocked_domains': domains(pr.findtext('blocked_domains')),
            'allowed_domains': domains(pr.findtext('allowed_domains')),
        }
    for d in node.findall('devices/device'):
        mac = norm_mac(d.findtext('mac') or '')
        if mac:
            cfg['devices'].append({
                'enabled': d.findtext('enabled') == '1',
                'group': d.findtext('group'),
                'mac': mac,
            })
    for b in node.findall('bedtimes/bedtime'):
        groups = b.findtext('groups') or b.findtext('group') or ''   # 'group': oude versie
        cfg['bedtimes'].append({
            'enabled': b.findtext('enabled') == '1',
            'groups': set(filter(None, groups.split(','))),
            'days': set(filter(None, (b.findtext('days') or '').split(','))),
            'start': parse_hhmm(b.findtext('start')),
            'end': parse_hhmm(b.findtext('end')),
        })
    for b in node.findall('budgets/budget'):
        try:
            minutes = int(b.findtext('minutes'))
        except (TypeError, ValueError):
            minutes = None
        cfg['budgets'].append({
            'enabled': b.findtext('enabled') == '1',
            'groups': set(filter(None, (b.findtext('groups') or '').split(','))),
            'days': set(filter(None, (b.findtext('days') or '').split(','))),
            'minutes': minutes,
        })
    return cfg


# ---------------------------------------------------------------- daemon

class Daemon:
    def __init__(self):
        self.running = True
        self.lock = threading.RLock()   # beschermt gedeelde staat tussen tick en webserver
        self.cfg = None
        self.cfg_mtime = None
        self.loaded_rules = None
        self.page_failed = False         # blokkeerpagina-regels konden niet geladen worden
        self.dns_failed = False          # DNS-afdwinging kon niet geladen worden
        self.plan_cache = (0.0, None)    # (tijdstip, plan)
        self.last = {}        # tabelnaam -> set(ip) zoals nu in pf
        self.blocking = {}    # group uuid -> reden (voor logging)
        self.traffic = TrafficMeter()
        self.timers = {}
        self.states = {}      # group uuid -> status voor de GUI
        self.limits = {}
        self.web_ips = {}     # ip -> {'uuid', 'name', 'reason'} voor de blokkeerpagina
        self.web_servers = []
        self.web_port = None
        self.usage = UsageStore(USAGE_FILE)
        self.requests = RequestStore(REQUESTS_FILE)
        self.syncer = pc_adguard.Syncer(lambda msg: log(msg))
        self.loaded_plan = None
        self.last_mono = None
        self.last_save = time.monotonic()

    # ---- config en pf

    def load_config(self):
        try:
            mtime = os.stat(CONFIG_XML).st_mtime
            if self.cfg is not None and mtime == self.cfg_mtime:
                return
            self.cfg = parse_config(ET.parse(CONFIG_XML).getroot())
            self.cfg_mtime = mtime
            self.page_failed = False
            self.dns_failed = False
            self.plan_cache = (0.0, None)
            self._anch_cache = (0.0, set())
            log('configuratie geladen')
        except (OSError, ET.ParseError) as err:
            log('config lezen mislukt: %s' % err, syslog.LOG_ERR)
            if self.cfg is None:
                self.cfg = dict(EMPTY_CFG)

    def sync_web(self):
        cfg = self.cfg
        want = cfg['page_port'] if (cfg['enabled'] and cfg['blockpage']) else None
        if want == self.web_port:
            return
        if self.web_servers:
            pc_web.stop(self.web_servers)
            self.web_servers = []
        self.web_port = want
        if want:
            self.web_servers = pc_web.start(self, want, log)
            if not self.web_servers:
                log('blokkeerpagina-server kon niet starten', syslog.LOG_ERR)

    def dns_plan(self):
        """Bepaal (gecached) of en hoe DNS-afdwinging moet worden toegepast, of None."""
        cfg = self.cfg
        if not (cfg['enabled'] and cfg['dns_enforce'] and cfg['dns_servers'] and cfg['dns_ifnames']):
            return None
        cached_at, plan = self.plan_cache
        if time.monotonic() - cached_at < PLAN_TTL and cached_at:
            return plan
        plan = None
        anchors = registered_anchors()
        if 'rdr' not in anchors or 'fw' not in anchors:
            log('DNS-afdwinging uitgeschakeld: het anchor "%s" staat niet in de firewallregels '
                '(klik op Toepassen of controleer /tmp/rules.debug)' % ANCHOR, syslog.LOG_ERR)
        else:
            nets = parse_ifconfig(run(['/sbin/ifconfig', '-a']).stdout)
            nat = []
            for ifname in cfg['dns_ifnames']:
                for srv in cfg['dns_servers']:
                    addr = ipaddress.ip_address(srv)
                    for net in nets.get(ifname, []):
                        if addr.version == net.version and addr in net:
                            entry = (ifname, str(net), addr.version)
                            if entry not in nat:
                                nat.append(entry)
            if nat and 'nat' not in anchors:
                log('DNS-afdwinging uitgeschakeld: de DNS-server staat in hetzelfde subnet als clients '
                    'maar het nat-anchor is niet geregistreerd', syslog.LOG_ERR)
            else:
                plan = {'servers': list(cfg['dns_servers']), 'ifs': list(cfg['dns_ifnames']), 'nat': nat,
                        'block_encrypted': cfg['dns_block_encrypted'],
                        'group_dns_block': cfg['dns_group_mode'] == 'block'}
                if nat:
                    log('DNS-server staat in hetzelfde subnet als clients: omgeleide queries worden '
                        'via de firewall genat (AdGuard ziet dan het firewall-adres)')
        self.plan_cache = (time.monotonic(), plan)
        return plan

    def ensure_anchor(self, groups):
        cfg = self.cfg
        want_page = bool(cfg['blockpage']) and not self.page_failed
        if want_page and 'rdr' not in self.plan_registered():
            log('blokkeerpagina uitgeschakeld: rdr-anchor staat niet in de firewallregels', syslog.LOG_ERR)
            self.page_failed = True
            want_page = False
        plan = None if self.dns_failed else self.dns_plan()
        port = cfg['page_port'] if want_page else None
        rules = build_ruleset(groups, port, cfg['dns_servers'], plan)
        anchor_empty = bool(groups or plan) and not pfctl(['-sr']).stdout.strip()
        if rules == self.loaded_rules and not anchor_empty:
            return True
        res = pfctl(['-f', '-'], rules)
        if res.returncode != 0 and plan:
            log('anchor met DNS-afdwinging laden mislukt (%s); terugval zonder DNS-afdwinging' %
                res.stderr.strip(), syslog.LOG_ERR)
            self.dns_failed = True
            plan = None
            rules = build_ruleset(groups, port, cfg['dns_servers'], None)
            res = pfctl(['-f', '-'], rules)
        if res.returncode != 0 and port:
            log('anchor met blokkeerpagina laden mislukt (%s); terugval op alleen blokkeren' %
                res.stderr.strip(), syslog.LOG_ERR)
            self.page_failed = True
            rules = build_ruleset(groups, None, cfg['dns_servers'], plan)
            res = pfctl(['-f', '-'], rules)
        if res.returncode != 0:
            log('anchor laden mislukt: %s' % res.stderr.strip(), syslog.LOG_ERR)
            return False
        self.loaded_rules = rules
        self.loaded_plan = plan
        # Tables met 'persist' behouden hun inhoud bij herladen; lees die terug.
        self.last = {g['table']: table_show(g['table']) for g in groups}
        if plan and plan.get('group_dns_block'):
            self.last['pc_pdev'] = table_show('pc_pdev')
        return True

    def plan_registered(self):
        """Geregistreerde anchors, gecached samen met het DNS-plan (max PLAN_TTL seconden)."""
        cached_at, anchors = getattr(self, '_anch_cache', (0.0, set()))
        if cached_at and time.monotonic() - cached_at < PLAN_TTL:
            return anchors
        anchors = registered_anchors()
        self._anch_cache = (time.monotonic(), anchors)
        return anchors

    # ---- tick

    def tick(self):
        self.load_config()
        cfg = self.cfg
        groups = [g for g in cfg['groups'].values() if g['enabled']] if cfg['enabled'] else []
        self.sync_web()
        if not self.ensure_anchor(groups):
            # laat de GUI zien dat de daemon draait, ook al kan pf niet bijgewerkt worden
            self.write_status(groups, datetime.now(), {})
            return
        now = datetime.now()
        day = now.date().isoformat()
        ts = time.time()
        self.process_queue(cfg, day)
        self.requests.expire(ts)
        mono = time.monotonic()
        dt = 0.0 if self.last_mono is None else min(mono - self.last_mono, 2.0 * cfg['interval'])
        self.last_mono = mono

        # Reden van blokkeren per groep: 'bedtime', 'exhausted' (tijd op) of None
        limits, reasons = {}, {}
        for g in groups:
            uuid = g['uuid']
            limits[uuid] = daily_limit(cfg['budgets'], uuid, now)
            if any(window_active(w, now) for w in cfg['bedtimes']
                   if w['enabled'] and uuid in w['groups']):
                reasons[uuid] = 'bedtime'
            elif (limits[uuid] is not None and
                  self.usage.get(day, uuid) >= limits[uuid] + self.usage.get_extra(day, uuid)):
                reasons[uuid] = 'exhausted'
            else:
                reasons[uuid] = None
        self.limits = limits

        neigh = neighbours() if groups else {}
        group_ips = {g['uuid']: set() for g in groups}
        for d in cfg['devices']:
            if d['enabled'] and d['group'] in group_ips:
                group_ips[d['group']] |= neigh.get(d['mac'], set())

        self.configure_backend(groups, group_ips)
        self.update_pdev(groups, group_ips)

        web_ips = {}
        for g in groups:
            reason = reasons[g['uuid']]
            if reason:
                for ip in group_ips[g['uuid']]:
                    web_ips[ip] = {'uuid': g['uuid'], 'name': g['name'], 'reason': reason}
            self.apply(g, group_ips[g['uuid']] if reason else set(), reason)
        self.web_ips = web_ips

        if groups:
            self.measure(groups, group_ips, reasons, dt, now)
        self.write_status(groups, now, limits)
        if self.usage.dirty and mono - self.last_save >= USAGE_SAVE_INTERVAL:
            self.usage.save()
            self.last_save = mono

    # ---- commando's uit de GUI

    def process_queue(self, cfg, day):
        try:
            names = sorted(n for n in os.listdir(QUEUE_DIR) if n.endswith('.json') and not n.startswith('.'))
        except OSError:
            return
        for name in names:
            path = os.path.join(QUEUE_DIR, name)
            try:
                with open(path) as handle:
                    cmd = json.load(handle)
            except (OSError, ValueError):
                cmd = {}
            try:
                os.remove(path)
            except OSError:
                pass
            action = cmd.get('action')
            if action == 'add_time':
                uuid, minutes = cmd.get('group'), cmd.get('minutes')
                if uuid in cfg['groups'] and isinstance(minutes, int) and 0 < minutes <= 720:
                    self.usage.add_extra(day, uuid, minutes * 60)
                    self.usage.save()
                    log('groep "%s": %d minuten extra tijd toegevoegd' % (cfg['groups'][uuid]['name'], minutes))
                    continue
            elif action == 'resolve_request':
                req = self.requests.get(str(cmd.get('id', '')))
                if req and req['status'] == 'pending' and req['group'] in cfg['groups']:
                    approve = bool(cmd.get('approve'))
                    self.requests.resolve(req['id'], approve, time.time())
                    if approve:
                        self.usage.add_extra(day, req['group'], req['minutes'] * 60)
                        self.usage.save()
                    log('groep "%s": verzoek om %d minuten %s' %
                        (req['name'], req['minutes'], 'goedgekeurd' if approve else 'afgewezen'))
                    continue
            log('ongeldig commando genegeerd: %s' % name, syslog.LOG_WARNING)

    def queue_pending(self):
        try:
            return any(n.endswith('.json') and not n.startswith('.') for n in os.listdir(QUEUE_DIR))
        except OSError:
            return False

    # ---- blokkeerpagina (aangeroepen vanuit de webserver-threads)

    def page_info(self, ip):
        with self.lock:
            entry = self.web_ips.get(ip)
            if not entry:
                return {'blocked': False}
            cfg = self.cfg or EMPTY_CFG
            now = datetime.now()
            ts = time.time()
            day = now.date().isoformat()
            uuid, reason = entry['uuid'], entry['reason']
            info = {
                'blocked': True, 'reason': reason, 'group_name': entry['name'], 'uuid': uuid,
                'used': self.usage.get(day, uuid), 'limit': self.limits.get(uuid),
                'extra': self.usage.get_extra(day, uuid), 'until': None,
                'can_request': False, 'req_state': 'none', 'cooldown_left': 0,
                'request_enabled': cfg['request_enabled'], 'minutes_options': REQUEST_OPTIONS,
            }
            if reason == 'bedtime':
                info['until'] = bedtime_until(cfg['bedtimes'], uuid, now)
            last = self.requests.latest(uuid)
            if last:
                cooldown = cfg['request_cooldown'] * 60
                info['cooldown_left'] = max(0, int(last['created'] + cooldown - ts))
                if last['status'] == 'pending':
                    info['req_state'] = 'pending'
                elif last['status'] in ('approved', 'denied') and info['cooldown_left'] > 0:
                    info['req_state'] = last['status']
            info['can_request'] = (reason == 'exhausted' and cfg['request_enabled'] and
                                   info['req_state'] != 'pending' and info['cooldown_left'] <= 0)
            return info

    def create_request(self, ip, minutes):
        with self.lock:
            if minutes not in REQUEST_OPTIONS:
                return {'ok': False, 'message': 'Ongeldig aantal minuten.'}
            info = self.page_info(ip)
            if not info['blocked'] or info['reason'] != 'exhausted':
                return {'ok': False, 'message': 'Je kunt alleen extra tijd vragen als je tijd op is.'}
            if not info['request_enabled']:
                return {'ok': False, 'message': 'Extra tijd vragen is uitgeschakeld.'}
            if info['req_state'] == 'pending':
                return {'ok': False, 'message': 'Er staat al een verzoek open.'}
            if info['cooldown_left'] > 0:
                return {'ok': False, 'message': 'Je kunt over %d minuten opnieuw vragen.' %
                        -(-info['cooldown_left'] // 60)}
            req = self.requests.add(info['uuid'], info['group_name'], minutes)
            log('groep "%s": verzoek om %d minuten extra tijd' % (req['name'], minutes))
            threading.Thread(target=self._notify_request, args=(req, info, dict(self.cfg['notify'])),
                             daemon=True).start()
            return {'ok': True, 'message': 'Verzoek verstuurd.'}

    def _notify_request(self, req, info, notify_cfg):
        total = fmt_dur((info['limit'] or 0) + info['extra'])
        message = '%s vraagt %d minuten extra tijd (verbruikt %s van %s). Beslis in Ouderlijk toezicht.' % (
            req['name'], req['minutes'], fmt_dur(info['used']), total)
        errors = notify(notify_cfg, 'Verzoek extra tijd: %s' % req['name'], message,
                        click=notify_cfg.get('gui_url') or None, event='extra_time_request')
        for err in errors:
            log('melding mislukt: %s' % err, syslog.LOG_WARNING)

    # ---- meten, status, pf

    def measure(self, groups, group_ips, reasons, dt, now):
        cfg = self.cfg
        ip_group = {}
        for uuid, ips in group_ips.items():
            for ip in ips:
                ip_group.setdefault(ip, set()).add(uuid)
        states = parse_states(run([PFCTL, '-ss', '-vv']).stdout) if ip_group else []
        deltas = self.traffic.update(states, ip_group, cfg['interval'])
        threshold = max(1.0, cfg['threshold_kb'] * 1024 * dt / 60.0)
        day = now.date().isoformat()
        for g in groups:
            uuid = g['uuid']
            active = reasons[uuid] is None and dt > 0 and deltas.get(uuid, 0) >= threshold
            timer = self.timers.setdefault(uuid, GroupTimer())
            secs = timer.update(active, dt, cfg['grace_min'] * 60)
            if secs:
                self.usage.add(day, uuid, secs)
            self.states[uuid] = reasons[uuid] or timer.state(active)

    def write_status(self, groups, now, limits):
        day = now.date().isoformat()
        cutoff = (now.date() - timedelta(days=8)).isoformat()
        status = {
            'updated': time.time(),
            'interval': self.cfg['interval'],
            'date': day,
            'groups': {
                g['uuid']: {
                    'name': g['name'],
                    'state': self.states.get(g['uuid'], 'inactive'),
                    'used_today': self.usage.get(day, g['uuid']),
                    'limit': limits.get(g['uuid']),
                    'extra': self.usage.get_extra(day, g['uuid']),
                } for g in groups
            },
            'history': {k: v for k, v in self.usage.days.items() if k >= cutoff},
            'backend': self.syncer.get_status(),
        }
        try:
            atomic_write(STATUS_FILE, status)
        except OSError as err:
            log('status schrijven mislukt: %s' % err, syslog.LOG_ERR)

    def set_table(self, table, want, kill=True):
        """Maak de pf-table gelijk aan 'want'. Geeft False terug als pf dat weigert."""
        have = self.last.get(table, set())
        if want == have:
            return True
        if want:
            res = pfctl(['-t', table, '-T', 'replace'] + sorted(want))
        else:
            res = pfctl(['-t', table, '-T', 'flush'])
        if res.returncode != 0:
            log('table %s bijwerken mislukt: %s' % (table, res.stderr.strip()), syslog.LOG_ERR)
            return False
        if kill:
            # Nieuwe adressen: bestaande verbindingen verbreken. Verwijderde adressen:
            # states opruimen (o.a. de omleiding naar de blokkeerpagina).
            for ip in sorted(want - have) + sorted(have - want):
                run([PFCTL, '-k', ip])
        self.last[table] = want
        return True

    def update_pdev(self, groups, group_ips):
        """Tabel met alle apparaten van groepen met een profiel (voor het blokkeren van eigen DNS)."""
        plan = self.loaded_plan
        if not (plan and plan.get('group_dns_block')):
            return
        ips = set()
        for g in groups:
            if self.cfg['profiles'].get(g.get('profile')):
                ips |= group_ips[g['uuid']]
        self.set_table('pc_pdev', ips, kill=False)

    def configure_backend(self, groups, group_ips):
        """Geef de gewenste profielen door aan de filter-backend (AdGuard) in de achtergrondthread."""
        cfg = self.cfg
        if not (cfg['enabled'] and cfg['filter_backend'] == 'adguard' and cfg['adguard_url']):
            self.syncer.configure(None, None)
            return
        clients, keep = {}, set()
        for g in groups:
            profile = cfg['profiles'].get(g.get('profile'))
            if not profile:
                continue
            name = pc_adguard.PREFIX + g['name']
            ips = group_ips[g['uuid']]
            if ips:
                clients[name] = {'ids': set(ips), 'profile': profile}
            else:
                keep.add(name)           # geen bekende adressen: bestaande client ongemoeid laten
        settings = {'url': cfg['adguard_url'], 'user': cfg['adguard_user'],
                    'password': cfg['adguard_password'], 'verify_tls': cfg['adguard_verify_tls']}
        self.syncer.configure(settings, {'clients': clients, 'keep': keep})

    def apply(self, group, want, reason):
        table = group['table']
        if not self.set_table(table, want):
            return
        if self.blocking.get(group['uuid']) != reason:
            self.blocking[group['uuid']] = reason
            if reason:
                log('groep "%s": geblokkeerd (%s), %d adres(sen)' %
                    (group['name'], 'bedtijd' if reason == 'bedtime' else 'tijd op', len(want)))
            else:
                log('groep "%s": vrijgegeven' % group['name'])

    # ---- levenscyclus

    def stop(self, *_):
        self.running = False

    def loop(self):
        self.syncer.start()
        while self.running:
            try:
                with self.lock:
                    self.tick()
            except Exception as err:  # daemon mag nooit omvallen op een tick
                log('fout in tick: %r' % err, syslog.LOG_ERR)
            deadline = time.monotonic() + (self.cfg['interval'] if self.cfg else 10)
            while self.running and time.monotonic() < deadline:
                time.sleep(1)
                if self.queue_pending():
                    break
        self.syncer.stop()
        pc_web.stop(self.web_servers)
        self.usage.save()
        # Blokkades blijven bewust staan bij stoppen (geen gat bij herstart);
        # 'service.sh cleanup' maakt het anchor leeg.
        log('parentalcontrold gestopt')


def main():
    syslog.openlog('parentalcontrold', syslog.LOG_PID, syslog.LOG_DAEMON)
    daemon = Daemon()
    signal.signal(signal.SIGTERM, daemon.stop)
    signal.signal(signal.SIGINT, daemon.stop)
    log('parentalcontrold gestart')
    daemon.loop()


if __name__ == '__main__':
    sys.exit(main())
