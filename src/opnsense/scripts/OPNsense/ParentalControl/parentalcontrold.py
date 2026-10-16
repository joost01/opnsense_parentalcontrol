#!/usr/local/bin/python3
"""
parentalcontrold - ouderlijk toezicht voor OPNsense (stap 1: bedtijden).

Werking:
  * leest /conf/config.xml (model OPNsense/ParentalControl)
  * bepaalt per groep of er nu een bedtijd actief is
  * zoekt via ARP/NDP de huidige IPv4/IPv6-adressen van de apparaten (op MAC)
  * houdt per groep een pf-table bij in het anchor 'parentalcontrol';
    een vaste regel blokkeert al het verkeer van adressen in die table
  * ruimt bestaande states op voor nieuw geblokkeerde adressen
"""
import ipaddress
import os
import re
import signal
import subprocess
import sys
import syslog
import time
import xml.etree.ElementTree as ET
from datetime import datetime

CONFIG_XML = '/conf/config.xml'
ANCHOR = 'parentalcontrol'
PFCTL = '/sbin/pfctl'
DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']  # datetime.weekday() volgorde


def log(msg, level=syslog.LOG_NOTICE):
    syslog.syslog(level, msg)


def run(cmd, stdin=None):
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True)


# ---------------------------------------------------------------- helpers

def norm_mac(mac):
    """ndp/arp laten soms voorloopnullen weg: normaliseer naar aa:bb:cc:dd:ee:ff."""
    try:
        parts = mac.strip().lower().split(':')
        if len(parts) != 6:
            return None
        return ':'.join('%02x' % int(p, 16) for p in parts)
    except ValueError:
        return None


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


def norm_ip(text):
    try:
        return str(ipaddress.ip_address(text.strip().split('%')[0]))
    except ValueError:
        return None


def parse_arp(output):
    result = {}
    for line in output.splitlines():
        m = re.match(r'\S+ \(([0-9.]+)\) at ([0-9a-fA-F:]+) on', line)
        if m:
            mac, ip = norm_mac(m.group(2)), norm_ip(m.group(1))
            if mac and ip:
                result.setdefault(mac, set()).add(ip)
    return result


def parse_ndp(output):
    result = {}
    for line in output.splitlines():
        m = re.match(r'([0-9a-fA-F:]+)(?:%\S+)?\s+([0-9a-fA-F:]+)\s+\S+', line)
        if not m:
            continue
        mac, ip = norm_mac(m.group(2)), norm_ip(m.group(1))
        if not mac or not ip:
            continue
        addr = ipaddress.ip_address(ip)
        if addr.is_link_local or addr.is_multicast:
            continue
        result.setdefault(mac, set()).add(ip)
    return result


def neighbours():
    """mac -> set(ip), uit ARP (IPv4) en NDP (IPv6)."""
    result = {}
    for parsed in (parse_arp(run(['/usr/sbin/arp', '-an']).stdout),
                   parse_ndp(run(['/usr/sbin/ndp', '-an']).stdout)):
        for mac, ips in parsed.items():
            result.setdefault(mac, set()).update(ips)
    return result


def pfctl(args, stdin=None):
    return run([PFCTL, '-a', ANCHOR] + args, stdin)


def table_show(name):
    out = pfctl(['-t', name, '-T', 'show']).stdout
    return {ip for ip in (norm_ip(x) for x in out.split()) if ip}


def build_ruleset(groups):
    lines = ['table <%s> persist' % g['table'] for g in groups]
    lines += ['block drop in quick from <%s> to any' % g['table'] for g in groups]
    return '\n'.join(lines) + '\n'


# ---------------------------------------------------------------- config

def parse_config(root):
    node = root.find('OPNsense/ParentalControl')
    cfg = {'enabled': False, 'interval': 10, 'groups': {}, 'devices': [], 'bedtimes': []}
    if node is None:
        return cfg
    cfg['enabled'] = node.findtext('general/enabled') == '1'
    try:
        cfg['interval'] = max(5, min(60, int(node.findtext('general/tick_interval') or 10)))
    except ValueError:
        pass
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
        cfg['bedtimes'].append({
            'enabled': b.findtext('enabled') == '1',
            'group': b.findtext('group'),
            'days': set(filter(None, (b.findtext('days') or '').split(','))),
            'start': parse_hhmm(b.findtext('start')),
            'end': parse_hhmm(b.findtext('end')),
        })
    return cfg


# ---------------------------------------------------------------- daemon

class Daemon:
    def __init__(self):
        self.running = True
        self.cfg = None
        self.cfg_mtime = None
        self.loaded_rules = None
        self.last = {}       # tabelnaam -> set(ip) zoals nu in pf
        self.blocking = {}   # group uuid -> bool (voor logging)

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
                self.cfg = {'enabled': False, 'interval': 10, 'groups': {}, 'devices': [], 'bedtimes': []}

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

        active = {}
        for g in groups:
            active[g['uuid']] = any(
                window_active(w, now) for w in cfg['bedtimes']
                if w['enabled'] and w['group'] == g['uuid']
            )
        neigh = neighbours() if any(active.values()) else {}

        for g in groups:
            ips = set()
            if active[g['uuid']]:
                for d in cfg['devices']:
                    if d['enabled'] and d['group'] == g['uuid']:
                        ips |= neigh.get(d['mac'], set())
            self.apply(g, ips, active[g['uuid']])

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
        # Bij stoppen blijven de blokkades bewust staan (geen gat bij herstart);
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
