#!/usr/local/bin/python3
"""
parentalcontrold - ouderlijk toezicht voor OPNsense.

  * bedtijden: per groep een pf-table in het anchor 'parentalcontrol'; een vaste
    regel blokkeert verkeer van adressen in die table
  * tijdtelling: per tick wordt per groep EEN keer de verstreken tijd opgeteld als
    minstens een apparaat van de groep actief was (gelijktijdig gebruik telt dus
    niet dubbel). Activiteit = bytes-delta over de pf-states van de apparaten.
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
    def __init__(self, path):
        self.path = path
        self.days = {}
        self.dirty = False
        try:
            with open(path) as handle:
                self.days = json.load(handle).get('days', {})
        except (OSError, ValueError):
            pass

    def add(self, day, uuid, secs):
        entry = self.days.setdefault(day, {})
        entry[uuid] = round(entry.get(uuid, 0.0) + secs, 1)
        self.dirty = True

    def get(self, day, uuid):
        return self.days.get(day, {}).get(uuid, 0.0)

    def save(self):
        cutoff = (date.today() - timedelta(days=USAGE_KEEP_DAYS)).isoformat()
        self.days = {k: v for k, v in self.days.items() if k >= cutoff}
        try:
            atomic_write(self.path, {'days': self.days})
            self.dirty = False
        except OSError as err:
            log('verbruik opslaan mislukt: %s' % err, syslog.LOG_ERR)


# ---------------------------------------------------------------- config

def parse_config(root):
    node = root.find('OPNsense/ParentalControl')
    cfg = {'enabled': False, 'interval': 10, 'threshold_kb': 60, 'grace_min': 3,
           'groups': {}, 'devices': [], 'bedtimes': []}
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
    return cfg


EMPTY_CFG = {'enabled': False, 'interval': 10, 'threshold_kb': 60, 'grace_min': 3,
             'groups': {}, 'devices': [], 'bedtimes': []}


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
        mono = time.monotonic()
        dt = 0.0 if self.last_mono is None else min(mono - self.last_mono, 2.0 * cfg['interval'])
        self.last_mono = mono

        in_bedtime = {
            g['uuid']: any(window_active(w, now) for w in cfg['bedtimes']
                           if w['enabled'] and g['uuid'] in w['groups'])
            for g in groups
        }
        neigh = neighbours() if groups else {}
        group_ips = {g['uuid']: set() for g in groups}
        for d in cfg['devices']:
            if d['enabled'] and d['group'] in group_ips:
                group_ips[d['group']] |= neigh.get(d['mac'], set())

        for g in groups:
            blocked = in_bedtime[g['uuid']]
            self.apply(g, group_ips[g['uuid']] if blocked else set(), blocked)

        if groups:
            self.measure(groups, group_ips, in_bedtime, dt, now)
        self.write_status(groups, now)
        if self.usage.dirty and mono - self.last_save >= USAGE_SAVE_INTERVAL:
            self.usage.save()
            self.last_save = mono

    def measure(self, groups, group_ips, in_bedtime, dt, now):
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
            active = (not in_bedtime[uuid]) and dt > 0 and deltas.get(uuid, 0) >= threshold
            timer = self.timers.setdefault(uuid, GroupTimer())
            secs = timer.update(active, dt, cfg['grace_min'] * 60)
            if secs:
                self.usage.add(day, uuid, secs)
            self.states[uuid] = 'bedtime' if in_bedtime[uuid] else timer.state(active)

    def write_status(self, groups, now):
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
                } for g in groups
            },
            'history': {k: v for k, v in self.usage.days.items() if k >= cutoff},
        }
        try:
            atomic_write(STATUS_FILE, status)
        except OSError as err:
            log('status schrijven mislukt: %s' % err, syslog.LOG_ERR)

    def apply(self, group, want, is_active):
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
        if self.blocking.get(group['uuid']) != is_active:
            self.blocking[group['uuid']] = is_active
            log('groep "%s": bedtijd %s (%d adres(sen) geblokkeerd)' %
                (group['name'], 'actief' if is_active else 'voorbij', len(want)))

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
