import unittest

from helpers import FakeSystem, G1, IFCONFIG, group, make_daemon, xml_config

IFACES = ('<interfaces><lan><if>igb1</if></lan><opt1><if>igb2</if></opt1><wan><if>igb0</if></wan></interfaces>')
DNS = ('<dns_servers>192.168.1.2, 2001:db8:1::2</dns_servers><dns_enforce>1</dns_enforce>'
       '<dns_interfaces>lan,opt1</dns_interfaces><dns_block_encrypted>1</dns_block_encrypted>')


def daemon_with(general, fake=None):
    return make_daemon(xml_config(general=general, groups=group(G1, 'Kids'), interfaces=IFACES), fake)


class RulesetTests(unittest.TestCase):
    def setUp(self):
        self.ctx = daemon_with('')
        self.mod = self.ctx.mod

    def test_basic_groups_drop_silently(self):
        rules = self.mod.build_ruleset([{'table': 'pc_a'}])
        self.assertEqual(rules.splitlines(), ['table <pc_a> persist', 'block drop in quick from <pc_a> to any'])

    def test_block_page_with_external_dns_server(self):
        rules = self.mod.build_ruleset([{'table': 'pc_a'}], 8089, ['192.168.1.2'])
        self.assertIn('rdr pass inet proto tcp from <pc_a> to any port 80 -> 127.0.0.1 port 8089', rules)
        self.assertIn('pass in quick proto { tcp udp } from <pc_a> to { 192.168.1.2 } port 53', rules)
        self.assertNotIn('port 53 ->', rules)                       # no redirect to Unbound
        self.assertLess(rules.index('pass in quick'), rules.index('block return'))

    def test_block_page_without_dns_server_redirects_to_unbound(self):
        rules = self.mod.build_ruleset([{'table': 'pc_a'}], 8089)
        self.assertIn('-> 127.0.0.1 port 53', rules)

    def test_dns_enforcement_rule_order_and_content(self):
        plan = {'servers': ['192.168.1.2', '2001:db8:1::2'], 'ifs': ['igb1', 'igb2'],
                'nat': [('igb1', '192.168.1.0/24', 4)], 'block_encrypted': True}
        rules = self.mod.build_ruleset([{'table': 'pc_a'}], 8089, ['192.168.1.2'], plan)
        lines = rules.splitlines()

        def at(prefix):
            return next(i for i, line in enumerate(lines) if line.startswith(prefix))
        self.assertLess(at('table <pc_a>'), at('nat on'))
        self.assertLess(at('nat on'), at('no rdr'))
        self.assertLess(at('no rdr'), at('rdr pass on'))
        self.assertLess(at('rdr pass inet proto tcp'), at('block return in quick on'))
        self.assertLess(at('pass in quick'), at('block return in quick from'))
        self.assertIn('from ! <pc_dns_servers> to any port 53 -> 192.168.1.2 port 53', rules)
        self.assertIn('-> 2001:db8:1::2 port 53', rules)
        self.assertIn('port { 443 853 }', rules)
        self.assertNotIn('igb0', rules)                             # WAN nooit

    def test_ipv4_only_server_blocks_ipv6_dns(self):
        plan = {'servers': ['10.9.9.9'], 'ifs': ['igb1'], 'nat': [], 'block_encrypted': False}
        rules = self.mod.build_ruleset([], None, ['10.9.9.9'], plan)
        self.assertIn('block return in quick on { igb1 } inet6 proto { tcp udp } from ! <pc_dns_servers> '
                      'to any port 53', rules)
        self.assertNotIn('pc_doh', rules)
        self.assertNotIn('nat on', rules)

    def test_parse_ifconfig_skips_link_local(self):
        nets = self.mod.parse_ifconfig(IFCONFIG)
        self.assertEqual([str(n) for n in nets['igb1']], ['192.168.1.0/24', '2001:db8:1::/64'])
        self.assertNotIn('fe80', str(nets))


class DnsPlanTests(unittest.TestCase):
    def test_same_subnet_adds_nat_for_both_families(self):
        plan = daemon_with(DNS).d.dns_plan()
        self.assertEqual(plan['nat'], [('igb1', '192.168.1.0/24', 4), ('igb1', '2001:db8:1::/64', 6)])
        self.assertEqual(plan['ifs'], ['igb1', 'igb2'])

    def test_other_subnet_needs_no_nat(self):
        ctx = daemon_with(DNS.replace('192.168.1.2, 2001:db8:1::2', '10.50.0.2'))
        self.assertEqual(ctx.d.dns_plan()['nat'], [])

    def test_missing_anchors_disable_enforcement(self):
        fake = FakeSystem()
        fake.main_nat = 'rdr-anchor "parentalcontrol" all\n'          # nat anchor missing, hairpin needed
        ctx = daemon_with(DNS, fake)
        self.assertIsNone(ctx.d.dns_plan())
        self.assertTrue(any('nat anchor' in m for m in ctx.logs))
        fake = FakeSystem()
        fake.main_nat = ''
        ctx = daemon_with(DNS, fake)
        self.assertIsNone(ctx.d.dns_plan())
        self.assertTrue(any('not in the firewall rules' in m for m in ctx.logs))

    def test_disabled_without_servers_or_interfaces(self):
        self.assertIsNone(daemon_with(DNS.replace('<dns_enforce>1', '<dns_enforce>0')).d.dns_plan())
        self.assertIsNone(daemon_with(DNS.replace('lan,opt1', '')).d.dns_plan())


class FallbackTests(unittest.TestCase):
    groups = [{'table': 'pc_a', 'uuid': 'u', 'name': 'N'}]

    def test_fallback_chain(self):
        fake = FakeSystem()
        fake.load_error = lambda rules: 'syntax error' if 'pc_dns_servers' in rules else None
        ctx = daemon_with(DNS, fake)
        self.assertTrue(ctx.d.ensure_anchor(self.groups))
        self.assertTrue(ctx.d.dns_failed and not ctx.d.page_failed)
        self.assertIn('rdr pass', fake.loaded[-1])

        fake = FakeSystem()
        fake.load_error = lambda rules: 'syntax error' if 'rdr pass' in rules or 'pc_dns' in rules else None
        ctx = daemon_with(DNS, fake)
        self.assertTrue(ctx.d.ensure_anchor(self.groups))
        self.assertTrue(ctx.d.dns_failed and ctx.d.page_failed)
        self.assertIn('block drop', fake.loaded[-1])
        loads = len(fake.loaded)
        ctx.d.ensure_anchor(self.groups)
        self.assertEqual(len(fake.loaded), loads, 'no reload loop after the fallback')

    def test_total_failure_reports_false(self):
        fake = FakeSystem()
        fake.load_error = lambda rules: 'boom'
        self.assertFalse(daemon_with(DNS, fake).d.ensure_anchor(self.groups))

    def test_loads_for_dns_enforcement_without_groups(self):
        fake = FakeSystem()
        ctx = daemon_with(DNS, fake)
        self.assertTrue(ctx.d.ensure_anchor([]))
        self.assertIn('pc_dns_servers', fake.loaded[-1])

    def test_reloads_when_anchor_was_flushed(self):
        fake = FakeSystem()
        ctx = daemon_with('', fake)
        ctx.d.load_config()
        ctx.d.ensure_anchor(self.groups)
        count = len(fake.loaded)
        fake.anchor_loaded = False                                    # someone flushed the anchor
        ctx.d.ensure_anchor(self.groups)
        self.assertEqual(len(fake.loaded), count + 1)


if __name__ == '__main__':
    unittest.main()
