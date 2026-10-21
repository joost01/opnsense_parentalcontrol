#!/usr/local/bin/python3
"""
Geeft een JSON-lijst van apparaten die OPNsense kent:
DHCP-reserveringen (ISC, Kea, Dnsmasq), actieve DHCP-leases en de ARP/NDP-tabel.
"""
import csv
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pc_common import neighbours, norm_mac  # noqa: E402

KEA_CONFIG = '/usr/local/etc/kea/kea-dhcp4.conf'
CONFIG_XML = '/conf/config.xml'
ISC_LEASES = '/var/dhcpd/var/db/dhcpd.leases'
KEA_LEASES = '/var/db/kea/kea-leases4.csv'
DNSMASQ_LEASES = '/var/db/dnsmasq.leases'


def reservations(root):
    """[(mac, ip, naam)] uit config.xml."""
    found = []
    for iface in root.findall('dhcpd/*'):                               # ISC dhcpd
        for sm in iface.findall('staticmap'):
            found.append((sm.findtext('mac'), sm.findtext('ipaddr'),
                          sm.findtext('hostname') or sm.findtext('descr')))
    for r in root.findall('OPNsense/Kea/dhcp4/reservations/reservation'):   # Kea
        found.append((r.findtext('hw_address'), r.findtext('ip_address'),
                      r.findtext('hostname') or r.findtext('description')))
    for h in root.findall('OPNsense/Dnsmasq/hosts'):                    # Dnsmasq
        for mac in (h.findtext('hwaddr') or '').split(','):
            found.append((mac, h.findtext('ip'), h.findtext('host') or h.findtext('descr')))
    return found


def kea_config_reservations(path):
    """[(mac, ip, naam)] uit de Kea-configuratie die de DHCP-server werkelijk gebruikt."""
    with open(path) as handle:
        text = handle.read()
    text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)                       # Kea staat commentaar toe
    text = '\n'.join(l for l in text.splitlines() if not l.lstrip().startswith(('#', '//')))
    try:
        dhcp4 = json.loads(text).get('Dhcp4', {})
    except ValueError:
        return []                                                             # geen leesbare Kea-config: overslaan
    found = []
    pools = [dhcp4.get('reservations', [])] + [sub.get('reservations', []) for sub in dhcp4.get('subnet4', [])]
    for pool in pools:
        for r in pool:
            found.append((r.get('hw-address'), r.get('ip-address'), r.get('hostname')))
    return found


def isc_leases(text):
    """[(mac, ip, naam)] van actieve leases; latere blokken overschrijven eerdere."""
    per_ip = {}
    for m in re.finditer(r'lease ([0-9.]+) \{(.*?)\n\}', text, re.S):
        body = m.group(2)
        state = re.search(r'binding state (\w+);', body)
        mac = re.search(r'hardware ethernet ([0-9a-fA-F:]+);', body)
        host = re.search(r'client-hostname "([^"]*)";', body)
        if state and state.group(1) == 'active' and mac:
            per_ip[m.group(1)] = (mac.group(1), m.group(1), host.group(1) if host else '')
        else:
            per_ip.pop(m.group(1), None)
    return list(per_ip.values())


def kea_leases(path):
    found = []
    with open(path, newline='') as handle:
        for row in csv.DictReader(handle):
            try:
                if row.get('state', '0') == '0' and int(row.get('expire', 0)) > time.time():
                    found.append((row['hwaddr'], row['address'], (row.get('hostname') or '').rstrip('.')))
            except (ValueError, KeyError):
                continue
    return found


def dnsmasq_leases(path):
    found = []
    with open(path) as handle:
        for line in handle:
            parts = line.split()
            if len(parts) >= 4:
                found.append((parts[1], parts[2], '' if parts[3] == '*' else parts[3]))
    return found


def read(path, func):
    try:
        if os.path.exists(path):
            with open(path) as handle:
                return func(handle.read()) if func is isc_leases else func(path)
    except (OSError, ValueError, csv.Error):
        pass
    return []


def collect(reserved, leases, live):
    devices = {}

    def entry(mac):
        return devices.setdefault(mac, {'mac': mac, 'name': '', 'ips': set(), 'sources': []})

    def add(mac, ip, name, source):
        mac = norm_mac(mac)
        if not mac:
            return
        e = entry(mac)
        if ip:
            e['ips'].add(ip)
        if name and not e['name']:
            e['name'] = name
        if source not in e['sources']:
            e['sources'].append(source)

    for mac, ip, name in reserved:
        add(mac, ip, name, 'reservering')
    for mac, ip, name in leases:
        add(mac, ip, name, 'lease')
    for mac, ips in live.items():
        for ip in ips:
            add(mac, ip, '', 'actief')
        if not ips:
            add(mac, None, '', 'actief')
    rows = [{'mac': d['mac'], 'name': d['name'], 'ips': sorted(d['ips']),
             'sources': d['sources']} for d in devices.values()]
    rows.sort(key=lambda r: (r['name'] == '', r['name'].lower(), r['mac']))
    return rows


def main():
    debug = '--debug' in sys.argv
    errors, sources = [], {}

    def gather(name, func):
        try:
            sources[name] = func()
        except Exception as err:  # een kapotte bron mag de rest niet blokkeren
            sources[name] = []
            errors.append('%s: %r' % (name, err))

    gather('config.xml-reserveringen', lambda: reservations(ET.parse(CONFIG_XML).getroot()))
    gather('kea-config', lambda: kea_config_reservations(KEA_CONFIG) if os.path.exists(KEA_CONFIG) else [])
    gather('isc-leases', lambda: read(ISC_LEASES, isc_leases))
    gather('kea-leases', lambda: read(KEA_LEASES, kea_leases))
    gather('dnsmasq-leases', lambda: read(DNSMASQ_LEASES, dnsmasq_leases))
    gather('arp/ndp', lambda: neighbours(skip_permanent=True))
    reserved = sources['config.xml-reserveringen'] + sources['kea-config']
    leases = sources['isc-leases'] + sources['kea-leases'] + sources['dnsmasq-leases']
    rows = collect(reserved, leases, sources['arp/ndp'])
    if debug:
        for name, data in sources.items():
            print('%-26s %d item(s)' % (name, len(data)), file=sys.stderr)
        for err in errors:
            print('FOUT %s' % err, file=sys.stderr)
        for item in reserved:
            print('reservering: %r -> %s' % (item, norm_mac(item[0])), file=sys.stderr)
    print(json.dumps(rows))


if __name__ == '__main__':
    main()
