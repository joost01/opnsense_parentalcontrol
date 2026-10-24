"""Scenario tests: ticks, budgets, blocking, extra time, requests, block page, notifications."""
import json
import os
import socket
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

from helpers import G1, G2, SCRIPTS, T1, T2, FakeSystem, budget, device, group, make_daemon, xml_config

MAC1, MAC2, MAC3 = 'a0:b1:c2:d3:e4:f5', 'a0:b1:c2:d3:e4:f6', 'a0:b1:c2:d3:e4:f7'


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class Scenario:
    def __init__(self, general='', budgets=None):
        self.fake = FakeSystem()
        self.fake.neighbours = {MAC1: '192.168.1.20', MAC2: '192.168.1.21', MAC3: '192.168.1.22'}
        self.fake.traffic_ips = {'192.168.1.20', '192.168.1.21', '192.168.1.22'}
        xml = xml_config(
            general=general,
            groups=group(G1, 'Kids &amp; co') + group(G2, 'Teens'),
            devices=device('1', MAC1, G1) + device('2', MAC2, G1) + device('3', MAC3, G2),
            budgets=budgets if budgets is not None else budget(G1 + ',' + G2, 1))
        self.ctx = make_daemon(xml, self.fake)
        self.d = self.ctx.d
        self.day = self.ctx.mod.date.today().isoformat()

    def step(self, n=1):
        for _ in range(n):
            self.fake.n += 1
            self.d.tick()
            if self.d.last_mono is not None:
                self.d.last_mono -= 10        # laat elke tick 10 seconden duren

    def queue(self, script, *args):
        with open(os.path.join(SCRIPTS, script)) as handle:
            src = handle.read().replace('/var/db/parentalcontrol/queue', self.ctx.mod.QUEUE_DIR)
        path = os.path.join(self.ctx.tmp, script)
        with open(path, 'w') as handle:
            handle.write(src)
        return subprocess.run([sys.executable, path] + list(args), capture_output=True, text=True).stdout.strip()


class BudgetFlowTests(unittest.TestCase):
    def test_concurrent_devices_count_once_then_block_and_unblock(self):
        s = Scenario()
        s.step(4)                                        # 1 basislijn + 3 ticks actief
        self.assertAlmostEqual(s.d.usage.get(s.day, G1), 30, delta=0.01)    # two devices = counted once
        s.step(3)                                        # budget (60s) is op
        self.assertEqual(s.d.usage.get(s.day, G1), 60)
        s.step(1)
        self.assertEqual(s.fake.tables[T1], {'192.168.1.20', '192.168.1.21'})
        self.assertEqual(s.fake.tables[T2], {'192.168.1.22'})
        self.assertEqual(s.d.states[G1], 'exhausted')
        used = s.d.usage.get(s.day, G1)
        s.step(3)
        self.assertEqual(s.d.usage.get(s.day, G1), used, 'no time counted while blocked')

        self.assertEqual(s.queue('addtime.py', G1, '15'), 'OK')
        self.assertTrue(s.d.queue_pending())
        s.step(1)
        self.assertEqual(s.fake.tables[T1], set())       # G1 vrij
        self.assertEqual(s.fake.tables[T2], {'192.168.1.22'})
        self.assertEqual(s.d.usage.get_extra(s.day, G1), 900)
        self.assertIn('192.168.1.20', s.fake.killed)     # states opgeruimd bij blokkeren

    def test_status_is_written_and_usage_persisted(self):
        s = Scenario()
        s.step(3)
        with open(s.ctx.mod.STATUS_FILE) as handle:
            status = json.load(handle)
        self.assertEqual(status['groups'][G1]['limit'], 60)
        s.d.usage.save()
        self.assertGreater(s.ctx.mod.UsageStore(s.ctx.mod.USAGE_FILE).get(s.day, G1), 0)

    def test_status_still_written_when_pf_rejects_rules(self):
        s = Scenario()
        s.fake.load_error = lambda rules: 'boom'
        s.step(1)
        self.assertTrue(os.path.exists(s.ctx.mod.STATUS_FILE))

    def test_invalid_queue_commands_are_ignored(self):
        s = Scenario()
        os.makedirs(s.ctx.mod.QUEUE_DIR)
        with open(os.path.join(s.ctx.mod.QUEUE_DIR, '1.json'), 'w') as handle:
            json.dump({'action': 'add_time', 'group': 'nope', 'minutes': 15}, handle)
        with open(os.path.join(s.ctx.mod.QUEUE_DIR, '2.json'), 'w') as handle:
            handle.write('{not json')
        s.step(2)
        self.assertEqual(s.d.usage.extra, {})
        self.assertEqual(len([m for m in s.ctx.logs if 'invalid command' in m]), 2)
        self.assertEqual(os.listdir(s.ctx.mod.QUEUE_DIR), [])


class CaptureHandler(BaseHTTPRequestHandler):
    received = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        CaptureHandler.received.append((self.path, self.headers.get('Authorization'), body))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


class RequestFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.notify_port = free_port()
        cls.notify_srv = HTTPServer(('127.0.0.1', cls.notify_port), CaptureHandler)
        threading.Thread(target=cls.notify_srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.notify_srv.shutdown()
        cls.notify_srv.server_close()

    def setUp(self):
        CaptureHandler.received.clear()
        self.port = free_port()
        general = ('<page_port>%d</page_port><request_cooldown>15</request_cooldown>'
                   '<ntfy_server>http://127.0.0.1:%d</ntfy_server><ntfy_topic>t</ntfy_topic>'
                   '<ntfy_token>tok</ntfy_token><webhook_url>http://127.0.0.1:%d/hook</webhook_url>'
                   '<gui_url>https://fw.example/ui/parentalcontrol</gui_url>'
                   % (self.port, self.notify_port, self.notify_port))
        self.s = Scenario(general=general, budgets=budget(G1, 0))
        self.s.fake.neighbours = {MAC1: '127.0.0.1'}               # de testclient is localhost
        self.s.fake.traffic_ips = set()
        self.s.step(3)
        self.base = 'http://127.0.0.1:%d' % self.port

    def tearDown(self):
        self.s.ctx.mod.pc_web.stop(self.s.d.web_servers)

    def get(self, path, method='GET', data=None, headers=None):
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers or {})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode(), dict(resp.headers)

    def test_full_request_flow(self):
        s = self.s
        self.assertEqual(s.d.states[G1], 'exhausted')
        status, page, headers = self.get('/hotspot-detect.html')
        self.assertEqual(status, 200)
        self.assertIn('Kids &amp; co', page)                         # groepsnaam is ge-escaped
        self.assertIn('onclick="ask(15)', page)
        self.assertIn('no-store', headers['Cache-Control'])
        key = json.loads(self.get('/__pc/status')[1])['key']

        answer = json.loads(self.get('/__pc/request', 'POST', b'minutes=30')[1])
        self.assertTrue(answer['ok'])
        self.assertIn('Your request has been sent', self.get('/x')[1])
        self.assertNotEqual(json.loads(self.get('/__pc/status')[1])['key'], key)
        self.assertFalse(json.loads(self.get('/__pc/request', 'POST', b'minutes=30')[1])['ok'])  # al open
        self.assertFalse(json.loads(self.get('/__pc/request', 'POST', b'minutes=999')[1])['ok'])

        deadline = time.time() + 5
        while len(CaptureHandler.received) < 2 and time.time() < deadline:
            time.sleep(0.1)
        ntfy = next(r for r in CaptureHandler.received if r[0] == '/')
        self.assertEqual((ntfy[1], ntfy[2]['topic']), ('Bearer tok', 't'))
        self.assertIn('30 minutes', ntfy[2]['message'])
        self.assertEqual(ntfy[2]['click'], 'https://fw.example/ui/parentalcontrol')
        self.assertTrue(any(r[0] == '/hook' and r[2]['event'] == 'extra_time_request'
                            for r in CaptureHandler.received))

        rid = s.d.requests.items[0]['id']
        self.assertEqual(s.queue('resolve_request.py', rid, 'approve'), 'OK')
        s.step(1)
        self.assertEqual(s.d.usage.get_extra(s.day, G1), 1800)
        self.assertEqual(s.fake.tables[T1], set())

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        req = urllib.request.Request(self.base + '/abc?x=1', headers={'Host': 'example.com'})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.build_opener(NoRedirect).open(req, timeout=5)
        self.assertEqual((caught.exception.code, caught.exception.headers['Location']),
                         (302, 'http://example.com/abc?x=1'))

    def test_cooldown_and_denial(self):
        s = self.s
        self.assertTrue(json.loads(self.get('/__pc/request', 'POST', b'minutes=15')[1])['ok'])
        rid = s.d.requests.latest(G1)['id']
        s.queue('resolve_request.py', rid, 'deny')
        s.step(1)
        self.assertEqual(s.d.requests.get(rid)['status'], 'denied')
        self.assertEqual(s.d.states[G1], 'exhausted')
        info = s.d.page_info('127.0.0.1')
        self.assertFalse(info['can_request'])
        self.assertEqual(info['req_state'], 'denied')
        self.assertTrue(800 < info['cooldown_left'] <= 900)
        self.assertIn('rejected', self.get('/x')[1])
        s.d.requests.items[0]['created'] -= 1000                     # cooldown voorbij
        self.assertTrue(s.d.page_info('127.0.0.1')['can_request'])

    def test_bedtime_page_has_no_request_buttons(self):
        s = self.s
        s.d.web_ips['127.0.0.2'] = {'uuid': G2, 'name': 'Teens', 'reason': 'bedtime'}
        info = s.d.page_info('127.0.0.2')
        self.assertFalse(info['can_request'])
        self.assertNotIn('onclick="ask(', s.ctx.mod.pc_web.render_page(info))
        self.assertFalse(s.d.create_request('127.0.0.2', 15)['ok'])

    def test_connection_cap_is_in_place(self):
        pc_web = self.s.ctx.mod.pc_web
        self.assertEqual(pc_web.MAX_CONNECTIONS, 32)
        self.assertTrue(hasattr(self.s.d.web_servers[0], '_slots'))


if __name__ == '__main__':
    unittest.main()


class DayControlTests(unittest.TestCase):
    def setUp(self):
        self.s = Scenario()
        self.s.step(2)

    def command(self, name, group=G1):
        self.assertEqual(self.s.queue('group_cmd.py', group, name), 'OK')
        self.s.step(1)

    def events(self):
        with open(self.s.ctx.mod.EVENTS_FILE) as handle:
            return [json.loads(line) for line in handle]

    def test_block_day_blocks_regardless_of_budget_and_normal_releases(self):
        s = self.s
        self.command('block_day')
        self.assertEqual(s.d.states[G1], 'manual')
        self.assertEqual(s.fake.tables[T1], {'192.168.1.20', '192.168.1.21'})
        info = s.d.page_info('192.168.1.20')
        self.assertEqual(info['reason'], 'manual')
        self.assertFalse(info['can_request'])
        self.assertIn('A parent switched off', s.ctx.mod.pc_web.render_page(info))
        self.command('normal_day')
        self.assertNotEqual(s.d.states[G1], 'manual')
        self.assertEqual(s.d.usage.get_override(s.day, G1), None)

    def test_open_day_overrides_exhausted_budget(self):
        s = self.s
        s.step(8)                                       # budget of 1 minute is used up
        self.assertEqual(s.d.states[G1], 'exhausted')
        self.command('open_day')
        self.assertEqual(s.fake.tables[T1], set())
        self.assertEqual(s.d.usage.get_override(s.day, G1), 'open')
        self.assertEqual(s.d.states[G1], 'active')      # traffic keeps being counted

    def test_reset_counters_clears_usage_and_extra_time(self):
        s = self.s
        s.queue('addtime.py', G1, '15')
        s.step(8)
        self.assertGreater(s.d.usage.get(s.day, G1), 0)
        self.assertEqual(s.d.usage.get_extra(s.day, G1), 900)
        self.command('reset_counters')
        self.assertLess(s.d.usage.get(s.day, G1), 15)
        self.assertEqual(s.d.usage.get_extra(s.day, G1), 0)

    def test_overrides_are_per_group_and_expire_with_the_day(self):
        s = self.s
        self.command('block_day')
        self.assertIsNone(s.d.usage.get_override(s.day, G2))
        self.assertIsNone(s.d.usage.get_override('2000-01-01', G1))
        s.d.usage.save()
        again = s.ctx.mod.UsageStore(s.ctx.mod.USAGE_FILE)
        self.assertEqual(again.get_override(s.day, G1), 'block')

    def test_invalid_group_commands_are_rejected(self):
        for args in (['x', 'block_day'], [G1, 'explode'], [G1]):
            self.assertTrue(self.s.queue('group_cmd.py', *args).startswith('ERROR'), args)

    def test_events_are_recorded_and_status_exposes_override_and_ips(self):
        s = self.s
        self.command('block_day')
        self.command('normal_day')
        self.command('reset_counters')
        kinds = [(e['kind'], e.get('mode') or e.get('reason')) for e in self.events() if e['group'] == G1]
        self.assertIn(('override', 'block'), kinds)
        self.assertIn(('blocked', 'manual'), kinds)
        self.assertIn(('released', None), kinds)
        self.assertIn(('reset', None), kinds)
        s.d.usage.set_override(s.day, G1, 'open')
        s.step(1)
        with open(s.ctx.mod.STATUS_FILE) as handle:
            group = json.load(handle)['groups'][G1]
        self.assertEqual(group['override'], 'open')
        self.assertEqual(group['ips'], ['192.168.1.20', '192.168.1.21'])

    def test_event_log_is_pruned(self):
        s = self.s
        path = s.ctx.mod.EVENTS_FILE
        with open(path, 'w') as handle:
            handle.write(json.dumps({'ts': 1.0, 'group': G1, 'kind': 'old'}) + '\n')
            handle.write(json.dumps({'ts': 9999999999.0, 'group': G1, 'kind': 'future'}) + '\n')
        s.ctx.mod.EventLog(path)
        with open(path) as handle:
            self.assertEqual([json.loads(l)['kind'] for l in handle], ['future'])
