import json
import unittest

from fake_adguard import FakeAdGuard
from helpers import G1, G2, FakeSystem, device, make_daemon, parse, xml_config

P1 = 'cccccccc-1111-2222-3333-444444444444'
MAC1, MAC2, MAC3 = 'a0:b1:c2:d3:e4:f5', 'a0:b1:c2:d3:e4:f6', 'a0:b1:c2:d3:e4:f7'
IFACES = '<interfaces><lan><if>igb1</if></lan><wan><if>igb0</if></wan></interfaces>'
PROFILE = ('<profile uuid="%s"><name>10-12</name><safesearch>1</safesearch><parental>1</parental>'
           '<categories>social,games</categories><blocked_domains>bad.example, x y</blocked_domains>'
           '<allowed_domains>school.nl</allowed_domains></profile>' % P1)


def config(srv_url, password='secret', backend='adguard', group_mode='block', profile_for_g1=True):
    general = ('<filter_backend>%s</filter_backend><backend_url>%s</backend_url><backend_user>admin</backend_user>'
               '<backend_password>%s</backend_password><dns_servers>192.168.1.2</dns_servers><dns_enforce>1</dns_enforce>'
               '<dns_interfaces>lan</dns_interfaces><dns_group_mode>%s</dns_group_mode>'
               % (backend, srv_url, password, group_mode))
    groups = ('<group uuid="%s"><enabled>1</enabled><name>Kids</name>%s</group>'
              '<group uuid="%s"><enabled>1</enabled><name>Parents</name></group>'
              % (G1, '<profile>%s</profile>' % P1 if profile_for_g1 else '', G2))
    xml = xml_config(general=general, groups=groups, interfaces=IFACES,
                     devices=device('1', MAC1, G1) + device('2', MAC2, G1) + device('3', MAC3, G2))
    return xml.replace('<groups>', '<profiles>%s</profiles><groups>' % PROFILE, 1)


class ProfileConfigTests(unittest.TestCase):
    def test_parse_profiles_and_backend_settings(self):
        ctx = make_daemon(config('http://x:3000'))
        cfg = ctx.d.cfg
        profile = cfg['profiles'][P1]
        self.assertEqual(profile['categories'], {'social', 'games'})
        self.assertEqual(profile['blocked_domains'], ['bad.example'])        # invalid domain was filtered out
        self.assertEqual(profile['allowed_domains'], ['school.nl'])
        self.assertTrue(profile['safesearch'] and profile['parental'])
        self.assertEqual(cfg['groups'][G1]['profile'], P1)
        self.assertEqual(cfg['groups'][G2]['profile'], '')
        self.assertEqual((cfg['filter_backend'], cfg['backend_user'], cfg['dns_group_mode']),
                         ('adguard', 'admin', 'block'))

    def test_legacy_adguard_settings_are_still_read(self):
        xml = config('http://x:3000').replace('backend_url', 'adguard_url').replace('backend_user', 'adguard_user') \
            .replace('backend_password', 'adguard_password')
        cfg = make_daemon(xml).d.cfg
        self.assertEqual((cfg['backend_url'], cfg['backend_user'], cfg['backend_password']),
                         ('http://x:3000', 'admin', 'secret'))

    def test_pihole_backend_selected_and_backend_groups_parsed(self):
        xml = config('https://pi.hole', backend='pihole').replace(
            '<categories>', '<backend_groups>Strict kids, Homework</backend_groups><categories>')
        ctx = make_daemon(xml)
        settings = ctx.mod.backend_settings(ctx.d.cfg)
        self.assertEqual((settings['backend'], settings['url']), ('pihole', 'https://pi.hole'))
        self.assertEqual(ctx.d.cfg['profiles'][P1]['backend_groups'], ['Strict kids', 'Homework'])
        self.assertIsNone(ctx.mod.backend_settings(make_daemon(config('https://x', backend='none')).d.cfg))

    def test_defaults_without_profile_settings(self):
        cfg = make_daemon(xml_config()).d.cfg
        self.assertEqual((cfg['filter_backend'], cfg['dns_group_mode'], cfg['profiles']), ('none', 'block', {}))

    def test_ruleset_blocks_own_dns_for_profile_devices(self):
        plan = {'servers': ['192.168.1.2'], 'ifs': ['igb1'], 'nat': [], 'block_encrypted': False,
                'group_dns_block': True}
        rules = make_daemon(xml_config()).mod.build_ruleset([], None, [], plan)
        self.assertIn('table <pc_pdev> persist', rules)
        self.assertIn('no rdr on { igb1 } proto { tcp udp } from <pc_pdev> to any port 53', rules)
        self.assertIn('block return in quick on { igb1 } proto { tcp udp } from <pc_pdev> '
                      'to ! <pc_dns_servers> port 53', rules)
        lines = rules.splitlines()
        self.assertLess(next(i for i, l in enumerate(lines) if 'from <pc_pdev> to any' in l),
                        next(i for i, l in enumerate(lines) if l.startswith('rdr pass on')))
        plan['group_dns_block'] = False
        self.assertNotIn('pc_pdev', make_daemon(xml_config()).mod.build_ruleset([], None, [], plan))


class ProfileFlowTests(unittest.TestCase):
    def setUp(self):
        self.srv = FakeAdGuard()
        self.addCleanup(self.srv.close)
        self.fake = FakeSystem()
        self.fake.neighbours = {MAC1: '192.168.1.20', MAC2: '192.168.1.21', MAC3: '192.168.1.22'}
        self.ctx = make_daemon(config(self.srv.url), self.fake)
        self.d = self.ctx.d

    def read_status(self, ctx=None):
        with open((ctx or self.ctx).mod.STATUS_FILE) as handle:
            return json.load(handle)

    def tick(self):
        self.d.tick()
        self.d.last_mono = None if self.d.last_mono is None else self.d.last_mono - 10

    def test_profile_reaches_adguard_and_dns_is_locked_down(self):
        self.tick()
        self.assertTrue(self.d.syncer.sync_once(now=1000))
        self.assertEqual([c['name'] for c in self.srv.clients], ['PC: Kids'])        # Parents has no profile
        client = self.srv.clients[0]
        self.assertEqual(client['ids'], ['192.168.1.20', '192.168.1.21'])
        self.assertTrue(client['parental_enabled'] and client['safe_search']['enabled'])
        self.assertEqual(client['blocked_services'], ['discord'][:0] + ['facebook', 'instagram', 'steam', 'tiktok'])
        self.assertIn("||bad.example^$client='PC: Kids'", self.srv.rules)
        self.assertEqual(self.fake.tables['pc_pdev'], {'192.168.1.20', '192.168.1.21'})  # only devices with a profile
        self.assertEqual(self.d.loaded_plan['group_dns_block'], True)
        self.assertEqual(self.read_status()['backend']['state'], 'pending')      # the status is updated per tick
        self.tick()
        self.assertEqual(self.read_status()['backend']['state'], 'ok')

    def test_new_device_ip_updates_client_but_does_not_kill_connections(self):
        self.tick()
        self.d.syncer.sync_once(now=1000)
        self.fake.killed.clear()
        self.fake.neighbours[MAC2] = '192.168.1.99'                              # DHCP-wissel
        self.tick()
        self.assertTrue(self.d.syncer.sync_once(now=1100))
        self.assertEqual(self.srv.clients[0]['ids'], ['192.168.1.20', '192.168.1.99'])
        self.assertEqual(self.fake.tables['pc_pdev'], {'192.168.1.20', '192.168.1.99'})
        self.assertNotIn('192.168.1.99', self.fake.killed)

    def test_group_without_known_ips_keeps_existing_client(self):
        self.tick()
        self.d.syncer.sync_once(now=1000)
        self.fake.neighbours = {MAC3: '192.168.1.22'}                            # kinderapparaten offline
        self.tick()
        self.d.syncer.sync_once(now=1100)
        self.assertEqual([c['name'] for c in self.srv.clients], ['PC: Kids'])
        self.assertEqual(self.srv.clients[0]['ids'], ['192.168.1.20', '192.168.1.21'])

    def test_removing_profile_deletes_managed_client_only(self):
        self.srv.clients.append({'name': 'Eigen client', 'ids': ['10.0.0.9'], 'use_global_settings': True})
        self.tick()
        self.d.syncer.sync_once(now=1000)
        self.assertEqual(len(self.srv.clients), 2)
        ctx = make_daemon(config(self.srv.url, profile_for_g1=False), self.fake)
        ctx.d.syncer = self.d.syncer
        ctx.d.tick()
        self.d.syncer.sync_once(now=1100)
        self.assertEqual([c['name'] for c in self.srv.clients], ['Eigen client'])

    def test_backend_none_or_plugin_off_never_contacts_adguard(self):
        ctx = make_daemon(config(self.srv.url, backend='none'), self.fake)
        ctx.d.tick()
        self.assertEqual(ctx.d.syncer.get_status()['state'], 'disabled')
        self.assertFalse(ctx.d.syncer.sync_once(now=1000))
        self.assertEqual(self.srv.calls, [])

    def test_adguard_failure_does_not_stop_the_daemon(self):
        ctx = make_daemon(config(self.srv.url, password='wrong'), self.fake)
        ctx.d.tick()
        ctx.d.syncer.sync_once(now=1000)
        ctx.d.tick()                                                               # tick blijft werken
        status = self.read_status(ctx)
        self.assertEqual(status['backend']['state'], 'error')
        self.assertIn('login failed', status['backend']['message'])
        self.assertTrue(any('filter backend: error' in m for m in ctx.logs))

    def test_redirect_mode_does_not_create_pdev_table(self):
        ctx = make_daemon(config(self.srv.url, group_mode='redirect'), self.fake)
        ctx.d.tick()
        self.assertNotIn('pc_pdev', self.fake.loaded[-1])


if __name__ == '__main__':
    unittest.main()
