#!/usr/local/bin/python3
"""
parentalcontrold - parental control for OPNsense.

  * bedtimes: per group a pf table in the anchor 'parentalcontrol'; a fixed
    rule blocks traffic from addresses in that table
  * time counting: on every tick the elapsed time is added ONCE per group if
    at least one device of the group was active (simultaneous use therefore
    does not count twice). Activity = bytes delta over the pf states of the devices.
  * daily budgets: when the budget (plus extra time) of a group is used up, the group
    is blocked in the same way as during a bedtime
  * extra time: the GUI puts a command in a queue (QUEUE_DIR); the daemon processes
    it immediately and adds the minutes to the extra time of today
  * day controls: a parent can block a group for the rest of the day, open it for the whole day
    or reset the counters of today (queue command 'group_cmd')
  * event log: blocks, releases, requests and manual actions are appended to EVENTS_FILE
  * block page: blocked devices get DNS and HTTP (port 80) redirected to a
    local web server (pc_web) with an explanation and, when the time is up, the option to ask
    for extra time (at most one request per cooldown period). The parent decides in the GUI.
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
import pc_backends  # noqa: E402
import pc_web  # noqa: E402
from pc_common import fmt_dur, norm_ip, norm_mac, neighbours, run  # noqa: E402
from pc_i18n import _  # noqa: E402
from pc_notify import notify  # noqa: E402

CONFIG_XML = '/conf/config.xml'
USAGE_FILE = '/var/db/parentalcontrol/usage.json'
STATUS_FILE = '/var/run/parentalcontrol/status.json'
QUEUE_DIR = '/var/db/parentalcontrol/queue'
REQUESTS_FILE = '/var/db/parentalcontrol/requests.json'
EVENTS_FILE = '/var/db/parentalcontrol/events.jsonl'
EVENTS_KEEP_SECONDS = 35 * 86400
EVENTS_MAX_LINES = 5000
GROUP_COMMANDS = ('block_day', 'open_day', 'normal_day', 'reset_counters')
REQUEST_OPTIONS = (15, 30, 60)   # minutes a child can ask for
REQUEST_EXPIRY = 3600            # unanswered requests expire after an hour
# Well-known public DNS resolvers (for blocking DoH/DoT); not exhaustive.
DOH_IPS = ['8.8.8.8', '8.8.4.4', '1.1.1.1', '1.0.0.1', '9.9.9.9', '149.112.112.112',
           '208.67.222.222', '208.67.220.220', '94.140.14.14', '94.140.15.15',
           '2001:4860:4860::8888', '2001:4860:4860::8844', '2606:4700:4700::1111',
           '2606:4700:4700::1001', '2620:fe::fe', '2620:fe::9', '2620:119:35::35',
           '2620:119:53::53', '2a10:50c0::ad1:ff', '2a10:50c0::ad2:ff']
PLAN_TTL = 300                   # seconds the DNS plan (interfaces/subnets) stays cached
WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']   # msgids, translated when displayed
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


# ---------------------------------------------------------------- bedtimes

def parse_hhmm(value):
    try:
        h, m = (value or '').split(':')
        return int(h) * 60 + int(m)
    except ValueError:
        return None


def window_active(win, now):
    """
    win: {'days': set, 'start': minutes, 'end': minutes}
    A bedtime belongs to the day on which it starts. If end < start, it continues
    until the next morning. start == end is ignored.
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


def bedtime_until(bedtimes, uuid, now, tr=None):
    """Text such as 'Mon 07:00' for the next end of the bedtime (at most 48 hours ahead)."""
    tr = tr or _
    wins = [w for w in bedtimes if w['enabled'] and uuid in w['groups']]
    t = now.replace(second=0, microsecond=0)
    for _step in range(48 * 60):
        t += timedelta(minutes=1)
        if not any(window_active(w, t) for w in wins):
            if t.date() == now.date():
                return t.strftime('%H:%M')
            if t.date() == now.date() + timedelta(days=1):
                return tr('tomorrow %s') % t.strftime('%H:%M')
            return '%s %s' % (tr(WEEKDAYS[t.weekday()]), t.strftime('%H:%M'))
    return None


def daily_limit(budgets, uuid, now):
    """Lowest matching daily budget in seconds for this group, or None (unlimited)."""
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
    """-> {interface: [ip_network, ...]} (without link-local addresses)."""
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
    """Which kinds of 'parentalcontrol' anchors are in the active main rules: {'rdr','nat','fw'}."""
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
    """pf rules for DNS enforcement -> (tables, nat, rdr, filter)."""
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
        # devices with a profile: their own DNS server is blocked instead of redirected,
        # so the DNS filter keeps recognising the device and the profile cannot be bypassed
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
    With page_port: HTTP (port 80) from blocked devices is redirected to the
    block page and the rest is rejected ('return': fast error). DNS of blocked
    devices: with dns_servers only to those servers (e.g. AdGuard), otherwise redirected to
    Unbound on this firewall. Without page_port everything is silently dropped ('drop').
    plan: DNS enforcement for all clients on the selected interfaces (see Daemon.dns_plan).
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
    """Extract all IP addresses from the header line of a pf state."""
    ips = set()
    for tok in re.split(r'[\s()]+', line):
        tok = re.sub(r'\[\d+\]$', '', tok)          # IPv6: address[port]
        if tok.count(':') == 1 and '.' in tok:       # IPv4: address:port
            tok = tok.split(':')[0]
        ip = norm_ip(tok) if tok and (tok.count(':') >= 2 or tok.replace('.', '').isdigit()) else None
        if ip:
            ips.add(ip)
    return ips


def parse_states(output):
    """Parse 'pfctl -ss -vv' into [{'ips', 'bytes', 'age', 'key'}]."""
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
    """Computes the number of bytes per group since the previous measurement."""

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
                # (e.g. when the daemon starts): then only a baseline
                delta = total if st['age'] <= 2 * interval + 5 else 0
            else:
                delta = max(0, total - prev)
            for g in groups:
                per_group[g] = per_group.get(g, 0) + delta
        self.seen = new_seen
        return per_group


class GroupTimer:
    """
    Counts active time of a group. Inactive time right after activity
    (the 'grace period') only counts if activity resumes within the grace period;
    afterwards it is discarded. This way a session does not count for too long.
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
    """Usage and extra time per day per group (seconds)."""

    def __init__(self, path):
        self.path = path
        self.days = {}
        self.extra = {}
        self.overrides = {}     # day -> {uuid: 'block' | 'open'}
        self.dirty = False
        try:
            with open(path) as handle:
                data = json.load(handle)
            self.days = data.get('days', {})
            self.extra = data.get('extra', {})
            self.overrides = data.get('overrides', {})
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

    def get_override(self, day, uuid):
        return self.overrides.get(day, {}).get(uuid)

    def set_override(self, day, uuid, mode):
        """mode: 'block', 'open' or None (back to normal). Overrides end at midnight."""
        entry = self.overrides.setdefault(day, {})
        if mode:
            entry[uuid] = mode
        else:
            entry.pop(uuid, None)
        self.dirty = True

    def reset(self, day, uuid):
        """Reset the counters of today: time used and extra time."""
        self.days.setdefault(day, {})[uuid] = 0.0
        self.extra.setdefault(day, {})[uuid] = 0.0
        self.dirty = True

    def save(self):
        cutoff = (date.today() - timedelta(days=USAGE_KEEP_DAYS)).isoformat()
        self.days = {k: v for k, v in self.days.items() if k >= cutoff}
        self.extra = {k: v for k, v in self.extra.items() if k >= cutoff}
        self.overrides = {k: v for k, v in self.overrides.items() if k >= cutoff}
        try:
            atomic_write(self.path, {'days': self.days, 'extra': self.extra, 'overrides': self.overrides})
            self.dirty = False
        except OSError as err:
            log('saving usage failed: %s' % err, syslog.LOG_ERR)


class RequestStore:
    """Requests for extra time (pending/approved/denied/expired)."""

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
            log('saving requests failed: %s' % err, syslog.LOG_ERR)

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

    def expire(self, ts, on_expired=None):
        changed = False
        for req in self.items:
            if req['status'] == 'pending' and ts - req['created'] > REQUEST_EXPIRY:
                req['status'] = 'expired'
                req['resolved'] = ts
                changed = True
                if on_expired:
                    on_expired(req)
        if changed:
            self.save()


class EventLog:
    """Append-only JSON-lines log of what happened to a group (for the overview page)."""

    def __init__(self, path):
        self.path = path
        self.prune()

    def add(self, kind, group, **detail):
        record = dict(detail, ts=round(time.time(), 1), group=group, kind=kind)
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, 'a') as handle:
                handle.write(json.dumps(record) + '\n')
        except OSError as err:
            log('writing the event log failed: %s' % err, syslog.LOG_ERR)

    def prune(self):
        try:
            with open(self.path) as handle:
                lines = handle.read().splitlines()
        except OSError:
            return
        cutoff = time.time() - EVENTS_KEEP_SECONDS
        keep = []
        for line in lines[-EVENTS_MAX_LINES:]:
            try:
                if json.loads(line).get('ts', 0) >= cutoff:
                    keep.append(line)
            except ValueError:
                continue
        if len(keep) != len(lines):
            try:
                with open(self.path + '.tmp', 'w') as handle:
                    handle.write('\n'.join(keep) + ('\n' if keep else ''))
                os.replace(self.path + '.tmp', self.path)
            except OSError as err:
                log('pruning the event log failed: %s' % err, syslog.LOG_ERR)


# ---------------------------------------------------------------- config

EMPTY_CFG = {'enabled': False, 'interval': 10, 'threshold_kb': 60, 'grace_min': 3,
             'blockpage': True, 'page_port': 8089, 'dns_servers': [], 'dns_enforce': False, 'dns_block_encrypted': True,
             'dns_ifnames': [], 'request_enabled': True, 'request_cooldown': 15,
             'filter_backend': 'none', 'backend_url': '', 'backend_user': '', 'backend_password': '',
             'backend_verify_tls': True, 'dns_group_mode': 'block', 'profiles': {},
             'notify': {}, 'groups': {}, 'devices': [], 'bedtimes': [], 'budgets': []}


def backend_settings(cfg):
    """Connection settings for the selected filter backend, or None if none is configured."""
    if cfg['filter_backend'] not in pc_backends.BACKENDS:
        return None
    if not cfg['backend_url'] and cfg['filter_backend'] not in pc_backends.NO_URL_NEEDED:
        return None
    return {'backend': cfg['filter_backend'], 'url': cfg['backend_url'], 'user': cfg['backend_user'],
            'password': cfg['backend_password'], 'verify_tls': cfg['backend_verify_tls']}


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
    def general(field, legacy=None):
        value = node.findtext('general/' + field)
        if value is None and legacy:
            value = node.findtext('general/' + legacy)      # settings from before model 1.0.7
        return value

    cfg['backend_url'] = (general('backend_url', 'adguard_url') or '').strip()
    cfg['backend_user'] = general('backend_user', 'adguard_user') or ''
    cfg['backend_password'] = general('backend_password', 'adguard_password') or ''
    cfg['backend_verify_tls'] = (general('backend_verify_tls', 'adguard_verify_tls') or '1') == '1'
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
            'backend_groups': [n.strip() for n in (pr.findtext('backend_groups') or '').split(',') if n.strip()],
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
        self.lock = threading.RLock()   # protects shared state between tick and web server
        self.cfg = None
        self.cfg_mtime = None
        self.loaded_rules = None
        self.page_failed = False         # block page rules could not be loaded
        self.dns_failed = False          # DNS enforcement could not be loaded
        self.plan_cache = (0.0, None)    # (tijdstip, plan)
        self.last = {}        # tabelnaam -> set(ip) zoals nu in pf
        self.blocking = {}    # group uuid -> reason (for logging)
        self.traffic = TrafficMeter()
        self.timers = {}
        self.states = {}      # group uuid -> status for the GUI
        self.limits = {}
        self.web_ips = {}     # ip -> {'uuid', 'name', 'reason'} for the block page
        self.web_servers = []
        self.web_port = None
        self.usage = UsageStore(USAGE_FILE)
        self.requests = RequestStore(REQUESTS_FILE)
        self.events = EventLog(EVENTS_FILE)
        self.syncer = pc_backends.Syncer(lambda msg: log(msg))
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
            log('configuration loaded')
        except (OSError, ET.ParseError) as err:
            log('reading the configuration failed: %s' % err, syslog.LOG_ERR)
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
                log('the block page server could not be started', syslog.LOG_ERR)

    def dns_plan(self):
        """Determine (cached) whether and how DNS enforcement is applied, or None."""
        cfg = self.cfg
        if not (cfg['enabled'] and cfg['dns_enforce'] and cfg['dns_servers'] and cfg['dns_ifnames']):
            return None
        cached_at, plan = self.plan_cache
        if time.monotonic() - cached_at < PLAN_TTL and cached_at:
            return plan
        plan = None
        anchors = registered_anchors()
        if 'rdr' not in anchors or 'fw' not in anchors:
            log('DNS enforcement disabled: the anchor "%s" is not in the firewall rules '
                '(click Apply or check /tmp/rules.debug)' % ANCHOR, syslog.LOG_ERR)
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
                log('DNS enforcement disabled: the DNS server is in the same subnet as the clients '
                    'but the nat anchor is not registered', syslog.LOG_ERR)
            else:
                plan = {'servers': list(cfg['dns_servers']), 'ifs': list(cfg['dns_ifnames']), 'nat': nat,
                        'block_encrypted': cfg['dns_block_encrypted'],
                        'group_dns_block': cfg['dns_group_mode'] == 'block'}
                if nat:
                    log('DNS server is in the same subnet as the clients: redirected queries are '
                        'NATed through the firewall (the DNS filter then sees the firewall address)')
        self.plan_cache = (time.monotonic(), plan)
        return plan

    def ensure_anchor(self, groups):
        cfg = self.cfg
        want_page = bool(cfg['blockpage']) and not self.page_failed
        if want_page and 'rdr' not in self.plan_registered():
            log('block page disabled: the rdr anchor is not in the firewall rules', syslog.LOG_ERR)
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
            log('loading the anchor with DNS enforcement failed (%s); falling back to no DNS enforcement' %
                res.stderr.strip(), syslog.LOG_ERR)
            self.dns_failed = True
            plan = None
            rules = build_ruleset(groups, port, cfg['dns_servers'], None)
            res = pfctl(['-f', '-'], rules)
        if res.returncode != 0 and port:
            log('loading the anchor with the block page failed (%s); falling back to blocking only' %
                res.stderr.strip(), syslog.LOG_ERR)
            self.page_failed = True
            rules = build_ruleset(groups, None, cfg['dns_servers'], plan)
            res = pfctl(['-f', '-'], rules)
        if res.returncode != 0:
            log('loading the anchor failed: %s' % res.stderr.strip(), syslog.LOG_ERR)
            return False
        self.loaded_rules = rules
        self.loaded_plan = plan
        # Tables with 'persist' keep their contents on reload; read them back.
        self.last = {g['table']: table_show(g['table']) for g in groups}
        if plan and plan.get('group_dns_block'):
            self.last['pc_pdev'] = table_show('pc_pdev')
        return True

    def plan_registered(self):
        """Registered anchors, cached together with the DNS plan (at most PLAN_TTL seconds)."""
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
            # let the GUI show that the daemon is running even though pf could not be updated
            self.write_status(groups, datetime.now(), {}, {})
            return
        now = datetime.now()
        day = now.date().isoformat()
        ts = time.time()
        self.process_queue(cfg, day)
        self.requests.expire(ts, lambda req: self.events.add(
            'request_resolved', req['group'], minutes=req['minutes'], status='expired'))
        mono = time.monotonic()
        dt = 0.0 if self.last_mono is None else min(mono - self.last_mono, 2.0 * cfg['interval'])
        self.last_mono = mono

        # Reason for blocking per group: 'manual', 'bedtime', 'exhausted' (time is up) or None
        limits, reasons = {}, {}
        for g in groups:
            uuid = g['uuid']
            limits[uuid] = daily_limit(cfg['budgets'], uuid, now)
            override = self.usage.get_override(day, uuid)
            if override == 'block':
                reasons[uuid] = 'manual'
            elif override == 'open':
                reasons[uuid] = None
            elif any(window_active(w, now) for w in cfg['bedtimes']
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
        self.write_status(groups, now, limits, group_ips)
        if self.usage.dirty and mono - self.last_save >= USAGE_SAVE_INTERVAL:
            self.usage.save()
            self.last_save = mono

    # ---- commands from the GUI

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
                    log('group "%s": %d minutes of extra time added' % (cfg['groups'][uuid]['name'], minutes))
                    continue
            elif action == 'group_cmd':
                uuid, command = cmd.get('group'), cmd.get('cmd')
                if uuid in cfg['groups'] and command in GROUP_COMMANDS:
                    self.group_command(cfg['groups'][uuid], command, day)
                    continue
            elif action == 'resolve_request':
                req = self.requests.get(str(cmd.get('id', '')))
                if req and req['status'] == 'pending' and req['group'] in cfg['groups']:
                    approve = bool(cmd.get('approve'))
                    self.requests.resolve(req['id'], approve, time.time())
                    self.events.add('request_resolved', req['group'], minutes=req['minutes'],
                                    status='approved' if approve else 'rejected')
                    if approve:
                        self.usage.add_extra(day, req['group'], req['minutes'] * 60)
                        self.usage.save()
                    log('group "%s": request for %d minutes %s' %
                        (req['name'], req['minutes'], 'approved' if approve else 'rejected'))
                    continue
            log('invalid command ignored: %s' % name, syslog.LOG_WARNING)

    def group_command(self, group, command, day):
        """Apply a manual day control from the GUI to one group."""
        uuid = group['uuid']
        if command == 'reset_counters':
            self.usage.reset(day, uuid)
            self.timers[uuid] = GroupTimer()
            self.events.add('reset', uuid)
        else:
            mode = {'block_day': 'block', 'open_day': 'open', 'normal_day': None}[command]
            self.usage.set_override(day, uuid, mode)
            self.events.add('override', uuid, mode=mode or 'normal')
        self.usage.save()
        log('group "%s": %s' % (group['name'], command))

    def queue_pending(self):
        try:
            return any(n.endswith('.json') and not n.startswith('.') for n in os.listdir(QUEUE_DIR))
        except OSError:
            return False

    # ---- block page (called from the web server threads)

    def page_info(self, ip, tr=None):
        """State of the block page for one client; tr translates the texts that are built here."""
        tr = tr or _
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
                info['until'] = bedtime_until(cfg['bedtimes'], uuid, now, tr)
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

    def create_request(self, ip, minutes, tr=None):
        tr = tr or _
        with self.lock:
            if minutes not in REQUEST_OPTIONS:
                return {'ok': False, 'message': tr('Invalid number of minutes.')}
            info = self.page_info(ip, tr)
            if not info['blocked'] or info['reason'] != 'exhausted':
                return {'ok': False, 'message': tr('You can only ask for extra time when your time is up.')}
            if not info['request_enabled']:
                return {'ok': False, 'message': tr('Asking for extra time is disabled.')}
            if info['req_state'] == 'pending':
                return {'ok': False, 'message': tr('A request is already open.')}
            if info['cooldown_left'] > 0:
                return {'ok': False, 'message': tr('You can ask again in %d minutes.') %
                        -(-info['cooldown_left'] // 60)}
            req = self.requests.add(info['uuid'], info['group_name'], minutes)
            self.events.add('request', info['uuid'], minutes=minutes)
            log('group "%s": request for %d minutes of extra time' % (req['name'], minutes))
            threading.Thread(target=self._notify_request, args=(req, info, dict(self.cfg['notify'])),
                             daemon=True).start()
            return {'ok': True, 'message': tr('Request sent.')}

    def _notify_request(self, req, info, notify_cfg):
        total = fmt_dur((info['limit'] or 0) + info['extra'])
        message = _('%s asks for %d minutes of extra time (used %s of %s). Decide in Parental Control.') % (
            req['name'], req['minutes'], fmt_dur(info['used']), total)
        errors = notify(notify_cfg, _('Request for extra time: %s') % req['name'], message,
                        click=notify_cfg.get('gui_url') or None, event='extra_time_request')
        for err in errors:
            log('notification failed: %s' % err, syslog.LOG_WARNING)

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

    def write_status(self, groups, now, limits, group_ips):
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
                    'override': self.usage.get_override(day, g['uuid']),
                    'ips': sorted(group_ips.get(g['uuid'], ())),
                } for g in groups
            },
            'history': {k: v for k, v in self.usage.days.items() if k >= cutoff},
            'backend': self.syncer.get_status(),
        }
        try:
            atomic_write(STATUS_FILE, status)
        except OSError as err:
            log('writing the status failed: %s' % err, syslog.LOG_ERR)

    def set_table(self, table, want, kill=True):
        """Make the pf table equal to 'want'. Returns False if pf refuses."""
        have = self.last.get(table, set())
        if want == have:
            return True
        if want:
            res = pfctl(['-t', table, '-T', 'replace'] + sorted(want))
        else:
            res = pfctl(['-t', table, '-T', 'flush'])
        if res.returncode != 0:
            log('updating table %s failed: %s' % (table, res.stderr.strip()), syslog.LOG_ERR)
            return False
        if kill:
            # New addresses: break existing connections. Removed addresses:
            # clean up states (including the redirect to the block page).
            for ip in sorted(want - have) + sorted(have - want):
                run([PFCTL, '-k', ip])
        self.last[table] = want
        return True

    def update_pdev(self, groups, group_ips):
        """Table with all devices of groups with a profile (for blocking their own DNS)."""
        plan = self.loaded_plan
        if not (plan and plan.get('group_dns_block')):
            return
        ips = set()
        for g in groups:
            if self.cfg['profiles'].get(g.get('profile')):
                ips |= group_ips[g['uuid']]
        self.set_table('pc_pdev', ips, kill=False)

    def configure_backend(self, groups, group_ips):
        """Pass the desired profiles to the filter backend in the background thread."""
        cfg = self.cfg
        settings = backend_settings(cfg)
        if not (cfg['enabled'] and settings):
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
                keep.add(name)           # no known addresses: leave the existing client untouched
        self.syncer.configure(settings, {'clients': clients, 'keep': keep})

    def apply(self, group, want, reason):
        table = group['table']
        if not self.set_table(table, want):
            return
        if self.blocking.get(group['uuid']) != reason:
            previous = self.blocking.get(group['uuid'])
            self.blocking[group['uuid']] = reason
            if reason:
                label = {'bedtime': 'bedtime', 'manual': 'blocked by a parent'}.get(reason, 'time is up')
                log('group "%s": blocked (%s), %d address(es)' % (group['name'], label, len(want)))
                self.events.add('blocked', group['uuid'], reason=reason)
            else:
                log('group "%s": released' % group['name'])
                if previous is not None:
                    self.events.add('released', group['uuid'])

    # ---- lifecycle

    def stop(self, *unused):
        self.running = False

    def loop(self):
        self.syncer.start()
        while self.running:
            try:
                with self.lock:
                    self.tick()
            except Exception as err:  # the daemon must never fall over on a tick
                log('fout in tick: %r' % err, syslog.LOG_ERR)
            deadline = time.monotonic() + (self.cfg['interval'] if self.cfg else 10)
            while self.running and time.monotonic() < deadline:
                time.sleep(1)
                if self.queue_pending():
                    break
        self.syncer.stop()
        pc_web.stop(self.web_servers)
        self.usage.save()
        # Blocks deliberately stay in place when stopping (no gap on restart);
        # 'service.sh cleanup' flushes the anchor.
        log('parentalcontrold stopped')


def main():
    syslog.openlog('parentalcontrold', syslog.LOG_PID, syslog.LOG_DAEMON)
    daemon = Daemon()
    signal.signal(signal.SIGTERM, daemon.stop)
    signal.signal(signal.SIGINT, daemon.stop)
    log('parentalcontrold started')
    daemon.loop()


if __name__ == '__main__':
    sys.exit(main())
