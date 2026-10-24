import unittest

from fake_adguard import FakeAdGuard
from helpers import load

ag = load('pc_adguard_test', 'pc_adguard.py')
be = load('pc_backends_test', 'pc_backends.py')


def profile(**kw):
    base = {'safesearch': True, 'parental': True, 'categories': set(), 'blocked_domains': [], 'allowed_domains': []}
    base.update(kw)
    return base


def desired(**clients):
    return {'clients': {ag.PREFIX + name: {'ids': set(ids), 'profile': prof}
                        for name, (ids, prof) in clients.items()}, 'keep': set()}


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.srv = FakeAdGuard()
        self.addCleanup(self.srv.close)
        self.api = ag.AdGuard(self.srv.url, 'admin', 'secret')

    def test_creates_client_with_profile_settings(self):
        want = desired(Kids=(['192.168.1.20', '2001:db8::5'], profile(categories={'social', 'video', 'games'})))
        report = ag.sync(self.api, want)
        self.assertEqual((report['added'], report['updated'], report['deleted']), (1, 0, 0))
        client = self.srv.clients[0]
        self.assertEqual(client['name'], 'PC: Kids')
        self.assertEqual(client['ids'], ['192.168.1.20', '2001:db8::5'])
        self.assertFalse(client['use_global_settings'])
        self.assertTrue(client['parental_enabled'] and client['safesearch_enabled'])
        self.assertTrue(client['safe_search']['enabled'] and client['safe_search']['google'])
        self.assertIn('tiktok', client['blocked_services'])
        self.assertIn('youtube', client['blocked_services'])
        self.assertIn('steam', client['blocked_services'])
        self.assertNotIn('whatsapp', client['blocked_services'])      # messaging niet gekozen
        self.assertFalse(client['use_global_blocked_services'])

    def test_unknown_services_are_skipped_with_warning(self):
        report = ag.sync(self.api, desired(Kids=(['10.0.0.2'], profile(categories={'social'}))))
        self.assertEqual(self.srv.clients[0]['blocked_services'], ['facebook', 'instagram', 'tiktok'])
        self.assertTrue(report['warnings'] and 'snapchat' in report['warnings'][0])

    def test_second_sync_is_idempotent(self):
        want = desired(Kids=(['10.0.0.2'], profile(categories={'social'}, blocked_domains=['bad.example'])))
        ag.sync(self.api, want)
        writes = len(self.srv.writes())
        report = ag.sync(self.api, want)
        self.assertEqual((report['added'], report['updated'], report['deleted'], report['rules']), (0, 0, 0, False))
        self.assertEqual(len(self.srv.writes()), writes, 'no writes when the state is unchanged')

    def test_update_when_ips_change_and_delete_when_group_gone(self):
        ag.sync(self.api, desired(Kids=(['10.0.0.2'], profile())))
        report = ag.sync(self.api, desired(Kids=(['10.0.0.2', '10.0.0.3'], profile())))
        self.assertEqual(report['updated'], 1)
        self.assertEqual(self.srv.clients[0]['ids'], ['10.0.0.2', '10.0.0.3'])
        report = ag.sync(self.api, {'clients': {}, 'keep': set()})
        self.assertEqual(report['deleted'], 1)
        self.assertEqual(self.srv.clients, [])

    def test_keep_protects_clients_without_known_ips(self):
        ag.sync(self.api, desired(Kids=(['10.0.0.2'], profile())))
        ag.sync(self.api, {'clients': {}, 'keep': {'PC: Kids'}})
        self.assertEqual(len(self.srv.clients), 1)

    def test_never_touches_foreign_clients_or_rules(self):
        self.srv.clients.append({'name': 'My laptop', 'ids': ['10.0.0.99'], 'use_global_settings': True})
        ag.sync(self.api, desired(Kids=(['10.0.0.2'], profile(blocked_domains=['bad.example']))))
        ag.sync(self.api, {'clients': {}, 'keep': set()})
        self.assertEqual([c['name'] for c in self.srv.clients], ['My laptop'])
        self.assertEqual(self.srv.rules, ['||ads.example^', '! my own rule'])    # own rules intact

    def test_custom_domain_rules_in_managed_block(self):
        want = desired(Kids=(['10.0.0.2'], profile(blocked_domains=['Bad.Example', 'x y', '*.tracker.net'],
                                                  allowed_domains=['school.nl'])))
        ag.sync(self.api, want)
        self.assertEqual(self.srv.rules[:2], ['||ads.example^', '! my own rule'])
        block = self.srv.rules[2:]
        self.assertEqual(block[0], ag.RULES_BEGIN)
        self.assertEqual(block[-1], ag.RULES_END)
        self.assertIn("||bad.example^$client='PC: Kids'", block)
        self.assertIn("||tracker.net^$client='PC: Kids'", block)
        self.assertIn("@@||school.nl^$client='PC: Kids'", block)
        self.assertFalse(any('x y' in line for line in block))                          # invalid domain ignored

    def test_auth_failure_and_unreachable_are_reported(self):
        with self.assertRaises(ag.AdGuardError) as caught:
            ag.sync(ag.AdGuard(self.srv.url, 'admin', 'wrong'), desired())
        self.assertIn('login failed', str(caught.exception))
        dead = ag.AdGuard('http://127.0.0.1:1', 'a', 'b', timeout=1)
        with self.assertRaises(ag.AdGuardError) as caught:
            dead.status()
        self.assertIn('connection failed', str(caught.exception))
        with self.assertRaises(ag.AdGuardError):
            ag.AdGuard('ftp://x')

    def test_old_style_service_list_is_accepted(self):
        self.assertEqual(ag.services_for({'dating'}, {'tinder'}), (['tinder'], []))
        srv = FakeAdGuard(services=['facebook'])
        self.addCleanup(srv.close)
        self.assertEqual(ag.AdGuard(srv.url, 'admin', 'secret').service_ids(), {'facebook'})

    def test_replace_block_helpers(self):
        rules = ['a', ag.RULES_BEGIN, 'x', ag.RULES_END, 'b']
        self.assertEqual(ag.replace_block(rules, ['y']), ['a', 'b', ag.RULES_BEGIN, 'y', ag.RULES_END])
        self.assertEqual(ag.replace_block(rules, []), ['a', 'b'])


class SyncerTests(unittest.TestCase):
    def setUp(self):
        self.srv = FakeAdGuard()
        self.addCleanup(self.srv.close)
        self.logs = []
        self.syncer = be.Syncer(self.logs.append)
        self.settings = {'backend': 'adguard', 'url': self.srv.url, 'user': 'admin', 'password': 'secret', 'verify_tls': True}

    def test_sync_status_and_throttling(self):
        self.assertEqual(self.syncer.get_status()['state'], 'disabled')
        self.syncer.configure(self.settings, desired(Kids=(['10.0.0.2'], profile())))
        self.assertTrue(self.syncer.sync_once(now=1000))
        status = self.syncer.get_status()
        self.assertEqual(status['state'], 'ok')
        self.assertIn('v0.107.52', status['message'])
        self.syncer.configure(self.settings, desired(Kids=(['10.0.0.2', '10.0.0.3'], profile())))
        self.assertFalse(self.syncer.sync_once(now=1003), 'minimum interval between syncs')
        self.assertTrue(self.syncer.sync_once(now=1011))
        self.assertEqual(self.srv.clients[0]['ids'], ['10.0.0.2', '10.0.0.3'])
        self.assertFalse(self.syncer.sync_once(now=1100), 'nothing to do')

    def test_error_backoff_and_recovery(self):
        self.syncer.configure(dict(self.settings, password='wrong'), desired(Kids=(['10.0.0.2'], profile())))
        self.assertTrue(self.syncer.sync_once(now=1000))
        self.assertEqual(self.syncer.get_status()['state'], 'error')
        self.assertFalse(self.syncer.sync_once(now=1010), 'wait after an error')
        self.syncer.configure(self.settings, desired(Kids=(['10.0.0.2'], profile())))
        self.syncer.next_try = 0
        self.assertTrue(self.syncer.sync_once(now=1100))
        self.assertEqual(self.syncer.get_status()['state'], 'ok')
        self.assertTrue(any('error' in line for line in self.logs) and any(' ok ' in line for line in self.logs))

    def test_disabling_backend_sets_disabled(self):
        self.syncer.configure(self.settings, desired(Kids=(['10.0.0.2'], profile())))
        self.syncer.sync_once(now=1000)
        self.syncer.configure(None, None)
        self.assertEqual(self.syncer.get_status()['state'], 'disabled')
        self.assertFalse(self.syncer.sync_once(now=5000))


if __name__ == '__main__':
    unittest.main()
