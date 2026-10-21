"""Gedeelde testhulpen: laadt de daemon en bootst pfctl/arp/ndp/ifconfig na."""
import importlib.util
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from types import SimpleNamespace

SCRIPTS = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', 'src', 'opnsense', 'scripts',
                                        'OPNsense', 'ParentalControl'))
sys.path.insert(0, SCRIPTS)


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


G1 = 'aaaaaaaa-1111-2222-3333-444444444444'
G2 = 'bbbbbbbb-1111-2222-3333-444444444444'
T1 = 'pc_aaaaaaaa1111'
T2 = 'pc_bbbbbbbb1111'

IFCONFIG = """lo0: flags=1008049<UP,LOOPBACK,RUNNING,MULTICAST,LOWER_UP> metric 0 mtu 16384
\tinet 127.0.0.1 netmask 0xff000000
igb0: flags=1008843<UP,BROADCAST,RUNNING> metric 0 mtu 1500
\tinet 203.0.113.7 netmask 0xffffff00 broadcast 203.0.113.255
igb1: flags=1008843<UP,BROADCAST,RUNNING> metric 0 mtu 1500
\tinet 192.168.1.1 netmask 0xffffff00 broadcast 192.168.1.255
\tinet6 fe80::1%igb1 prefixlen 64 scopeid 0x2
\tinet6 2001:db8:1::1 prefixlen 64
igb2: flags=1008843<UP,BROADCAST,RUNNING> metric 0 mtu 1500
\tinet 192.168.20.1 netmask 0xffffff00 broadcast 192.168.20.255
"""


class FakeSystem:
    """Minimale emulatie van de systeemcommando's die de daemon aanroept."""

    def __init__(self):
        self.neighbours = {}            # mac -> ip
        self.traffic_ips = set()        # ips met verkeer
        self.n = 0                      # teller: bytes per state groeit met n
        self.tables = {}
        self.loaded = []                # alle geladen rulesets
        self.anchor_loaded = False
        self.killed = []
        self.load_error = lambda rules: None   # geef een foutmelding terug om pf te laten weigeren
        self.main_nat = 'rdr-anchor "parentalcontrol" all\nnat-anchor "parentalcontrol" all\n'
        self.main_rules = 'anchor "parentalcontrol" all\n'
        self.ifconfig = IFCONFIG

    def result(self, out='', rc=0, err=''):
        return SimpleNamespace(stdout=out, stderr=err, returncode=rc)

    def states(self):
        out = ''
        for i, ip in enumerate(sorted(self.traffic_ips)):
            out += ('all tcp %s:5000 -> 93.184.216.34:443  ESTABLISHED:ESTABLISHED\n'
                    '   age 00:00:03, expires in 1:00:00, 1:1 pkts, 10:%d bytes, rule 1\n'
                    '   id: 00000000000000%02x creatorid: 1\n' % (ip, self.n * 5_000_000, i))
        return out

    def run(self, cmd, stdin=None, timeout=20):
        prog = os.path.basename(cmd[0])
        if prog == 'arp':
            return self.result(''.join('? (%s) at %s on igb1 expires in 100 seconds [ethernet]\n' % (ip, mac)
                                       for mac, ip in self.neighbours.items()))
        if prog == 'ndp':
            return self.result('Neighbor Linklayer Address Netif Expire S Flags\n')
        if prog == 'ifconfig':
            return self.result(self.ifconfig)
        if prog != 'pfctl':
            return self.result('', 127, 'unknown command')
        if '-a' not in cmd:                      # hoofdregels
            if '-sn' in cmd:
                return self.result(self.main_nat)
            if '-sr' in cmd:
                return self.result(self.main_rules)
            if '-ss' in cmd:
                return self.result(self.states())
            if '-k' in cmd:
                self.killed.append(cmd[cmd.index('-k') + 1])
            return self.result()
        if '-sr' in cmd:
            return self.result('block\n' if self.anchor_loaded else '')
        if '-f' in cmd:
            self.loaded.append(stdin)
            err = self.load_error(stdin)
            if err:
                return self.result('', 1, err)
            self.anchor_loaded = True
            return self.result()
        if '-T' in cmd:
            table = cmd[cmd.index('-t') + 1]
            if 'replace' in cmd:
                self.tables[table] = set(cmd[cmd.index('replace') + 1:])
            elif 'flush' in cmd:
                self.tables[table] = set()
            elif 'show' in cmd:
                return self.result('\n'.join('   ' + ip for ip in sorted(self.tables.get(table, ()))))
        return self.result()


def make_daemon(config_xml, fake=None):
    """Laad de daemon met tijdelijke paden, nagebootste systeemcommando's en een logrecorder."""
    import pc_common
    daemon_mod = load('parentalcontrold_test', 'parentalcontrold.py')
    fake = fake or FakeSystem()
    tmp = tempfile.mkdtemp()
    cfg_path = os.path.join(tmp, 'config.xml')
    with open(cfg_path, 'w') as handle:
        handle.write(config_xml)
    daemon_mod.CONFIG_XML = cfg_path
    daemon_mod.USAGE_FILE = os.path.join(tmp, 'usage.json')
    daemon_mod.STATUS_FILE = os.path.join(tmp, 'run', 'status.json')
    daemon_mod.QUEUE_DIR = os.path.join(tmp, 'queue')
    daemon_mod.REQUESTS_FILE = os.path.join(tmp, 'requests.json')
    daemon_mod.run = fake.run
    pc_common.run = fake.run
    logs = []
    daemon_mod.log = lambda msg, level=0: logs.append(msg)
    daemon = daemon_mod.Daemon()
    daemon.load_config()
    return SimpleNamespace(mod=daemon_mod, d=daemon, fake=fake, logs=logs, tmp=tmp)


def xml_config(general='', groups='', devices='', bedtimes='', budgets='', interfaces=''):
    return ('<opnsense>%s<OPNsense><ParentalControl><general><enabled>1</enabled>%s</general>'
            '<groups>%s</groups><devices>%s</devices><bedtimes>%s</bedtimes><budgets>%s</budgets>'
            '</ParentalControl></OPNsense></opnsense>' % (interfaces, general, groups, devices, bedtimes, budgets))


def group(uuid, name):
    return '<group uuid="%s"><enabled>1</enabled><name>%s</name></group>' % (uuid, name)


def device(uuid, mac, group_uuid):
    return ('<device uuid="d-%s"><enabled>1</enabled><name>x</name><mac>%s</mac><group>%s</group></device>'
            % (uuid, mac, group_uuid))


def budget(groups, minutes, days='mon,tue,wed,thu,fri,sat,sun'):
    return ('<budget uuid="b-%s"><enabled>1</enabled><groups>%s</groups><days>%s</days><minutes>%d</minutes>'
            '</budget>' % (groups.replace(',', ''), groups, days, minutes))


def parse(xml):
    return ET.fromstring(xml)
