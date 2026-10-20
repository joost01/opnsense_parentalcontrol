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
"""
import json
import os
import re
import signal
import sys
import syslog
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
import ipaddress

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pc_common import norm_ip, norm_mac, neighbours, run  # noqa: E402

CONFIG_XML = '/conf/config.xml'
USAGE_FILE = '/var/db/parentalcontrol/usage.json'
STATUS_FILE = '/var/run/parentalcontrol/status.json'
QUEUE_DIR = '/var/db/parentalcontrol/queue'
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


def build_ruleset(groups):
    lines = ['table <%s> persist' % g['table'] for g in groups]
    lines += ['block drop in quick from <%s> to any' % g['table'] for g in groups]
    return '\n'.join(lines) + '\n'


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


# ---------------------------------------------------------------- config

def parse_config(root):
    node = root.find('OPNsense/ParentalControl')
    cfg = {'enabled': False, 'interval': 10, 'threshold_kb': 60, 'grace_min': 3,
           'groups': {}, 'devices': [], 'bedtimes': [], 'budgets': []}
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
    for g in node.findall('groups/group'):
        uuid = g.get('uuid')
        cfg['groups'][uuid] = {
            'uuid': uuid,
            'name': g.findtext('name') or uuid,
            'enabled': g.findtext('enabled') == '1',
            'table': table_name(uuid),
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


EMPTY_CFG = {'enabled': False, 'interval': 10, 'threshold_kb': 60, 'grace_min': 3,
             'groups': {}, 'devices': [], 'bedtimes': [], 'budgets': []}


# ---------------------------------------------------------------- daemon

class Daemon:
    def __init__(self):
        self.running = True
        self.cfg = None
        self.cfg_mtime = None
        self.loaded_rules = None
        self.last = {}        # tabelnaam -> set(ip) zoals nu in pf
        self.blocking = {}    # group uuid -> bool (voor logging)
        self.traffic = TrafficMeter()
        self.timers = {}
        self.states = {}      # group uuid -> status voor de GUI
        self.usage = UsageStore(USAGE_FILE)
        self.last_mono = None
        self.last_save = time.monotonic()

    def load_config(self):
        try:
            mtime = os.stat(CONFIG_XML).st_mtime
            if self.cfg is not None and mtime == self.cfg_mtime:
                return
            self.cfg = parse_config(ET.parse(CONFIG_XML).getroot())
            self.cfg_mtime = mtime
            log('configuratie geladen')
        except (OSError, ET.ParseError) as err:
            log('config lezen mislukt: %s' % err, syslog.LOG_ERR)
            if self.cfg is None:
                self.cfg = dict(EMPTY_CFG)

    def ensure_anchor(self, groups):
        rules = build_ruleset(groups)
        anchor_empty = bool(groups) and not pfctl(['-sr']).stdout.strip()
        if rules == self.loaded_rules and not anchor_empty:
            return True
        res = pfctl(['-f', '-'], rules)
        if res.returncode != 0:
            log('anchor laden mislukt: %s' % res.stderr.strip(), syslog.LOG_ERR)
            return False
        self.loaded_rules = rules
        # Tables met 'persist' behouden hun inhoud bij herladen; lees die terug.
        self.last = {g['table']: table_show(g['table']) for g in groups}
        return True

    def tick(self):
        self.load_config()
        cfg = self.cfg
        groups = [g for g in cfg['groups'].values() if g['enabled']] if cfg['enabled'] else []
        if not self.ensure_anchor(groups):
            return
        now = datetime.now()
        day = now.date().isoformat()
        self.process_queue(cfg, day)
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

        neigh = neighbours() if groups else {}
        group_ips = {g['uuid']: set() for g in groups}
        for d in cfg['devices']:
            if d['enabled'] and d['group'] in group_ips:
                group_ips[d['group']] |= neigh.get(d['mac'], set())

        for g in groups:
            reason = reasons[g['uuid']]
            self.apply(g, group_ips[g['uuid']] if reason else set(), reason)

        if groups:
            self.measure(groups, group_ips, reasons, dt, now)
        self.write_status(groups, now, limits)
        if self.usage.dirty and mono - self.last_save >= USAGE_SAVE_INTERVAL:
            self.usage.save()
            self.last_save = mono

    def process_queue(self, cfg, day):
        """Verwerk commando's uit de GUI (extra tijd toevoegen)."""
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
            uuid, minutes = cmd.get('group'), cmd.get('minutes')
            if (cmd.get('action') == 'add_time' and uuid in cfg['groups'] and
                    isinstance(minutes, int) and 0 < minutes <= 720):
                self.usage.add_extra(day, uuid, minutes * 60)
                self.usage.save()
                log('groep "%s": %d minuten extra tijd toegevoegd' % (cfg['groups'][uuid]['name'], minutes))
            else:
                log('ongeldig commando genegeerd: %s' % name, syslog.LOG_WARNING)

    def queue_pending(self):
        try:
            return any(n.endswith('.json') and not n.startswith('.') for n in os.listdir(QUEUE_DIR))
        except OSError:
            return False

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
        }
        try:
            atomic_write(STATUS_FILE, status)
        except OSError as err:
            log('status schrijven mislukt: %s' % err, syslog.LOG_ERR)

    def apply(self, group, want, reason):
        table = group['table']
        have = self.last.get(table, set())
        if want != have:
            if want:
                res = pfctl(['-t', table, '-T', 'replace'] + sorted(want))
            else:
                res = pfctl(['-t', table, '-T', 'flush'])
            if res.returncode != 0:
                log('table %s bijwerken mislukt: %s' % (table, res.stderr.strip()), syslog.LOG_ERR)
                return
            for ip in sorted(want - have):
                run([PFCTL, '-k', ip])  # bestaande verbindingen verbreken
            self.last[table] = want
        if self.blocking.get(group['uuid']) != reason:
            self.blocking[group['uuid']] = reason
            if reason:
                log('groep "%s": geblokkeerd (%s), %d adres(sen)' %
                    (group['name'], 'bedtijd' if reason == 'bedtime' else 'tijd op', len(want)))
            else:
                log('groep "%s": vrijgegeven' % group['name'])

    def stop(self, *_):
        self.running = False

    def loop(self):
        while self.running:
            try:
                self.tick()
            except Exception as err:  # daemon mag nooit omvallen op een tick
                log('fout in tick: %r' % err, syslog.LOG_ERR)
            deadline = time.monotonic() + (self.cfg['interval'] if self.cfg else 10)
            while self.running and time.monotonic() < deadline:
                time.sleep(1)
                if self.queue_pending():
                    break
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
