import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime

from helpers import G1, G2, SCRIPTS, budget, device, group, load, make_daemon, parse, xml_config

d = load('pcd_core', 'parentalcontrold.py')
kd = load('known_devices_test', 'known_devices.py')


def win(days, start, end):
    return {'days': set(days.split(',')), 'start': d.parse_hhmm(start), 'end': d.parse_hhmm(end)}


class ScheduleTests(unittest.TestCase):
    def test_same_day_window(self):
        w = win('mon', '13:00', '15:00')           # 2026-10-12 is een maandag
        self.assertTrue(d.window_active(w, datetime(2026, 10, 12, 14, 0)))
        self.assertFalse(d.window_active(w, datetime(2026, 10, 12, 15, 0)))
        self.assertFalse(d.window_active(w, datetime(2026, 10, 13, 14, 0)))

    def test_window_across_midnight_belongs_to_start_day(self):
        w = win('mon,tue,wed,thu', '21:00', '07:00')
        self.assertFalse(d.window_active(w, datetime(2026, 10, 15, 20, 59)))   # donderdag
        self.assertTrue(d.window_active(w, datetime(2026, 10, 15, 21, 0)))
        self.assertTrue(d.window_active(w, datetime(2026, 10, 16, 6, 59)))     # vrijdagochtend
        self.assertFalse(d.window_active(w, datetime(2026, 10, 16, 7, 0)))
        self.assertFalse(d.window_active(w, datetime(2026, 10, 16, 21, 30)))   # vrijdagavond: niet

    def test_equal_start_end_is_ignored(self):
        self.assertFalse(d.window_active(win('mon', '12:00', '12:00'), datetime(2026, 10, 12, 12, 0)))

    def test_bedtime_until(self):
        wins = [dict(win('mon', '21:00', '07:00'), enabled=True, groups={G1})]
        self.assertEqual(d.bedtime_until(wins, G1, datetime(2026, 10, 12, 22, 0)), 'morgen 07:00')
        self.assertEqual(d.bedtime_until(wins, G1, datetime(2026, 10, 13, 3, 0)), '07:00')

    def test_daily_limit_takes_lowest(self):
        budgets = [{'enabled': True, 'groups': {G1}, 'days': {'mon'}, 'minutes': 120},
                   {'enabled': True, 'groups': {G1, G2}, 'days': {'mon'}, 'minutes': 60},
                   {'enabled': False, 'groups': {G1}, 'days': {'mon'}, 'minutes': 5}]
        monday = datetime(2026, 10, 12, 12, 0)
        self.assertEqual(d.daily_limit(budgets, G1, monday), 3600)
        self.assertIsNone(d.daily_limit(budgets, G1, datetime(2026, 10, 13, 12, 0)))


class UsageTests(unittest.TestCase):
    def test_group_timer_counts_idle_gap_only_if_activity_resumes(self):
        t, total = d.GroupTimer(), 0
        for _ in range(3):
            total += t.update(True, 10, 180)
        for _ in range(2):
            total += t.update(False, 10, 180)
        self.assertEqual(total, 30)
        total += t.update(True, 10, 180)
        self.assertEqual(total, 60)                    # 10 + 20 naloop
        for _ in range(20):
            total += t.update(False, 10, 180)
        self.assertFalse(t.in_session)
        total += t.update(True, 10, 180)
        self.assertEqual(total, 70)                    # nieuwe sessie zonder oude naloop

    def test_parse_states_and_meter_count_group_once(self):
        sample = ('all tcp 192.168.1.10:51234 -> 93.184.216.34:443   ESTABLISHED:ESTABLISHED\n'
                  '   age 00:00:12, expires in 23:59:58, 10:12 pkts, 1000:50000 bytes, rule 100\n'
                  '   id: 6612a1b2c3d4e5f6 creatorid: 8a1b2c3d\n'
                  'all udp 203.0.113.5:40000 (192.168.1.11:40000) -> 8.8.8.8:53   MULTIPLE:SINGLE\n'
                  '   age 00:00:02, expires in 00:00:28, 2:2 pkts, 138:300 bytes, rule 12\n'
                  '   id: 0000000000000001 creatorid: 8a1b2c3d\n'
                  'all tcp 2001:db8::5[443] <- 2001:db8:1::77[50000]   ESTABLISHED:ESTABLISHED\n'
                  '   age 05:00:00, expires in 23:00:00, 900:900 pkts, 99999999:99999999 bytes, rule 7\n'
                  '   id: 00000000000000aa creatorid: 8a1b2c3d\n')
        states = d.parse_states(sample)
        self.assertEqual(len(states), 3)
        self.assertEqual(states[0]['bytes'], 51000)
        self.assertEqual(states[1]['ips'], {'203.0.113.5', '192.168.1.11', '8.8.8.8'})
        self.assertEqual(states[2]['ips'], {'2001:db8::5', '2001:db8:1::77'})
        ip_group = {'192.168.1.10': {'G'}, '192.168.1.11': {'G'}, '2001:db8:1::77': {'G'}}
        meter = d.TrafficMeter()
        self.assertEqual(meter.update(states, ip_group, 10), {'G': 51000 + 438})   # oude state = basislijn
        self.assertEqual(meter.update(states, ip_group, 10), {'G': 0})
        states[0]['bytes'] += 40000
        states[1]['bytes'] += 30000
        self.assertEqual(meter.update(states, ip_group, 10), {'G': 70000})

    def test_usage_and_request_store_roundtrip(self):
        tmp = tempfile.mkdtemp()
        store = d.UsageStore(os.path.join(tmp, 'u.json'))
        store.add('2026-10-16', 'G', 30)
        store.add('2026-10-16', 'G', 12.5)
        store.add_extra('2026-10-16', 'G', 900)
        store.save()
        again = d.UsageStore(os.path.join(tmp, 'u.json'))
        self.assertEqual((again.get('2026-10-16', 'G'), again.get_extra('2026-10-16', 'G')), (42.5, 900))
        requests = d.RequestStore(os.path.join(tmp, 'r.json'))
        req = requests.add('G', 'Kids', 30)
        self.assertTrue(requests.resolve(req['id'], True, 1.0))
        self.assertFalse(requests.resolve(req['id'], False, 2.0))      # al afgehandeld
        self.assertEqual(d.RequestStore(os.path.join(tmp, 'r.json')).get(req['id'])['status'], 'approved')

    def test_requests_expire(self):
        requests = d.RequestStore(os.path.join(tempfile.mkdtemp(), 'r.json'))
        req = requests.add('G', 'Kids', 15)
        req['created'] -= d.REQUEST_EXPIRY + 5
        requests.expire(req['created'] + d.REQUEST_EXPIRY + 10)
        self.assertEqual(req['status'], 'expired')


class ConfigTests(unittest.TestCase):
    def test_bedtime_multiple_groups_and_legacy_field(self):
        xml = xml_config(groups=group(G1, 'A') + group(G2, 'B'), bedtimes=(
            '<bedtime uuid="y"><enabled>1</enabled><groups>%s,%s</groups><days>mon</days><start>21:00</start>'
            '<end>07:00</end></bedtime>'
            '<bedtime uuid="z"><enabled>1</enabled><group>%s</group><days>tue</days><start>22:00</start>'
            '<end>06:00</end></bedtime>' % (G1, G2, G1)))
        cfg = d.parse_config(parse(xml))
        self.assertEqual(cfg['bedtimes'][0]['groups'], {G1, G2})
        self.assertEqual(cfg['bedtimes'][1]['groups'], {G1})

    def test_defaults_and_bounds(self):
        cfg = d.parse_config(parse(xml_config(general='<tick_interval>999</tick_interval>')))
        self.assertEqual((cfg['interval'], cfg['threshold_kb'], cfg['grace_min'], cfg['page_port']), (60, 60, 3, 8089))
        self.assertTrue(cfg['blockpage'] and cfg['request_enabled'] and not cfg['dns_enforce'])

    def test_dns_settings(self):
        cfg = d.parse_config(parse(xml_config(
            general='<dns_servers>192.168.1.2, fd00::2 , nonsense, 192.168.1.2</dns_servers>'
                    '<dns_enforce>1</dns_enforce><dns_interfaces>lan,nope,wan</dns_interfaces>',
            interfaces='<interfaces><lan><if>igb1</if></lan><wan><if>igb0</if></wan></interfaces>')))
        self.assertEqual(cfg['dns_servers'], ['192.168.1.2', 'fd00::2'])
        self.assertEqual(cfg['dns_ifnames'], ['igb1', 'igb0'])         # onbekende id wordt genegeerd


class ScriptTests(unittest.TestCase):
    def run_script(self, name, *args, queue=None):
        with open(os.path.join(SCRIPTS, name)) as handle:
            src = handle.read()
        if queue:
            src = src.replace('/var/db/parentalcontrol/queue', queue)
        path = os.path.join(tempfile.mkdtemp(), name)
        with open(path, 'w') as handle:
            handle.write(src)
        return subprocess.run([sys.executable, path] + list(args), capture_output=True, text=True).stdout.strip()

    def test_addtime_validates_and_queues(self):
        queue = os.path.join(tempfile.mkdtemp(), 'q')
        for bad in (['x', '15'], [G1, '0'], [G1, '9999'], [G1, 'abc'], [G1]):
            self.assertTrue(self.run_script('addtime.py', *bad, queue=queue).startswith('ERROR'), bad)
        self.assertEqual(self.run_script('addtime.py', G1, '15', queue=queue), 'OK')
        with open(os.path.join(queue, os.listdir(queue)[0])) as handle:
            item = json.load(handle)
        self.assertEqual(item, {'action': 'add_time', 'group': G1, 'minutes': 15})

    def test_resolve_request_validates(self):
        queue = os.path.join(tempfile.mkdtemp(), 'q')
        for bad in (['zz', 'approve'], ['abcdef123456', 'maybe'], ['../../etc', 'deny']):
            self.assertTrue(self.run_script('resolve_request.py', *bad, queue=queue).startswith('ERROR'), bad)
        self.assertEqual(self.run_script('resolve_request.py', 'abcdef123456', 'deny', queue=queue), 'OK')

    def test_norm_mac_formats(self):
        pc = load('pc_common_test', 'pc_common.py')
        for raw in ('AA:BB:CC:DD:EE:01', 'aa-bb-cc-dd-ee-01', 'aabbccddee01', 'aabb.ccdd.ee01', ' AA:bb:CC:dd:EE:1 '):
            self.assertEqual(pc.norm_mac(raw), 'aa:bb:cc:dd:ee:01', raw)
        self.assertEqual(pc.norm_mac('0:1b:2c:3d:4e:5f'), '00:1b:2c:3d:4e:5f')
        for bad in (None, '', 'zz:bb:cc:dd:ee:01', 'aa:bb:cc:dd:ee', 'aa:bb:cc:dd:ee:011', '(incomplete)'):
            self.assertIsNone(pc.norm_mac(bad), bad)

    def test_kea_reservations_from_config_xml_and_kea_json(self):
        xml = ('<opnsense><OPNsense><Kea><dhcp4><reservations><reservation uuid="r1"><subnet>s1</subnet>'
               '<ip_address>192.168.1.50</ip_address><hw_address>AA-BB-CC-DD-EE-01</hw_address>'
               '<hostname>sams-ipad</hostname></reservation></reservations></dhcp4></Kea></OPNsense></opnsense>')
        reserved = kd.reservations(parse(xml))
        rows = kd.collect(reserved, [], {})
        self.assertEqual((rows[0]['mac'], rows[0]['name'], rows[0]['ips']),
                         ('aa:bb:cc:dd:ee:01', 'sams-ipad', ['192.168.1.50']))
        path = os.path.join(tempfile.mkdtemp(), 'kea-dhcp4.conf')
        with open(path, 'w') as handle:
            json.dump({'Dhcp4': {'subnet4': [{'subnet': '192.168.1.0/24', 'reservations': [
                {'hw-address': 'aa:bb:cc:dd:ee:02', 'ip-address': '192.168.1.51', 'hostname': 'tv'}]}],
                'reservations': [{'hw-address': 'aa:bb:cc:dd:ee:03', 'ip-address': '192.168.1.52'}]}}, handle)
        self.assertEqual(kd.kea_config_reservations(path), [
            ('aa:bb:cc:dd:ee:03', '192.168.1.52', None), ('aa:bb:cc:dd:ee:02', '192.168.1.51', 'tv')])

    def test_kea_config_tolerates_comments_and_garbage(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, 'a.conf')
        with open(path, 'w') as handle:
            handle.write('# gegenereerd\n/* blok */\n{"Dhcp4": {"reservations": [{"hw-address": "aa:bb:cc:dd:ee:09",'
                         ' "ip-address": "10.0.0.9"}]}}\n')
        self.assertEqual(kd.kea_config_reservations(path), [('aa:bb:cc:dd:ee:09', '10.0.0.9', None)])
        with open(path, 'w') as handle:
            handle.write('geen json')
        self.assertEqual(kd.kea_config_reservations(path), [])

    def test_known_devices_sources_merge(self):
        leases = ('lease 192.168.1.20 {\n  binding state active;\n  hardware ethernet a0:b1:c2:d3:e4:f5;\n'
                  '  client-hostname "Sams-iPad";\n}\nlease 192.168.1.21 {\n  binding state free;\n'
                  '  hardware ethernet 11:22:33:44:55:66;\n}\n')
        leased = kd.isc_leases(leases)
        self.assertEqual(leased, [('a0:b1:c2:d3:e4:f5', '192.168.1.20', 'Sams-iPad')])
        reserved = kd.reservations(parse('<opnsense><dhcpd><lan><staticmap><mac>AA:BB:CC:DD:EE:01</mac>'
                                         '<ipaddr>192.168.1.50</ipaddr><hostname>nas</hostname></staticmap>'
                                         '</lan></dhcpd></opnsense>'))
        rows = kd.collect(reserved, leased, {'a0:b1:c2:d3:e4:f5': {'192.168.1.20', '2001:db8::9'},
                                             'de:ad:be:ef:00:01': {'192.168.1.99'}})
        by_mac = {r['mac']: r for r in rows}
        self.assertEqual(by_mac['aa:bb:cc:dd:ee:01']['sources'], ['reservering'])
        self.assertEqual(by_mac['a0:b1:c2:d3:e4:f5']['sources'], ['lease', 'actief'])
        self.assertEqual(by_mac['de:ad:be:ef:00:01']['name'], '')


if __name__ == '__main__':
    unittest.main()
