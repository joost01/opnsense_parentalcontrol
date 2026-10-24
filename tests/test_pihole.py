import unittest

from fake_pihole import FakePiHole
from helpers import load

be = load('pc_backends_pihole_test', 'pc_backends.py')
ph = load('pc_pihole_test', 'pc_pihole.py')


def profile(**kw):
    base = {'safesearch': False, 'parental': False, 'categories': set(), 'blocked_domains': [],
            'allowed_domains': [], 'backend_groups': []}
    base.update(kw)
    return base


def desired(**clients):
    return {'clients': {ph.PREFIX + n: {'ids': set(ids), 'profile': prof} for n, (ids, prof) in clients.items()},
            'keep': set()}


class PiHoleSyncTests(unittest.TestCase):
    def setUp(self):
        self.srv = FakePiHole()
        self.addCleanup(self.srv.close)
        self.settings = {'backend': 'pihole', 'url': self.srv.url, 'password': 'apppw', 'verify_tls': True}

    def run_sync(self, want):
        return be.connect_and_sync(self.settings, want)

    def test_creates_group_clients_and_domain_rules(self):
        want = desired(Kids=(['192.168.1.20', '2001:db8::5'],
                             profile(blocked_domains=['bad.example'], allowed_domains=['school.nl'],
                                     backend_groups=['Strict kids'])))
        label, report = self.run_sync(want)
        self.assertEqual(label, 'Pi-hole v6.1')
        self.assertEqual((report['added'], report['updated'], report['deleted']), (2, 0, 0))
        own = next(g['id'] for g in self.srv.groups if g['name'] == 'PC: Kids')
        self.assertEqual({c['client']: sorted(c['groups']) for c in self.srv.clients},
                         {'192.168.1.20': sorted([0, 7, own]), '2001:db8::5': sorted([0, 7, own])})
        self.assertTrue(all(c['comment'].startswith(ph.MARK) for c in self.srv.clients))
        rules = {(d['type'], d['domain']): d for d in self.srv.domains if d['comment'].startswith(ph.MARK)}
        self.assertEqual(set(rules), {('deny', r'(\.|^)bad\.example$'), ('allow', r'(\.|^)school\.nl$')})
        self.assertEqual(rules[('deny', r'(\.|^)bad\.example$')]['groups'], [own])
        self.assertEqual(self.srv.logins, self.srv.logouts, 'session always closed properly')

    def test_idempotent_second_sync(self):
        want = desired(Kids=(['192.168.1.20'], profile(blocked_domains=['bad.example'])))
        self.run_sync(want)
        before = (list(self.srv.clients), list(self.srv.domains), list(self.srv.groups))
        _, report = self.run_sync(want)
        self.assertEqual((report['added'], report['updated'], report['deleted'], report['rules']), (0, 0, 0, False))
        self.assertEqual((self.srv.clients, self.srv.domains, self.srv.groups), before)

    def test_ip_change_and_cleanup_when_group_disappears(self):
        self.run_sync(desired(Kids=(['192.168.1.20'], profile(blocked_domains=['bad.example']))))
        _, report = self.run_sync(desired(Kids=(['192.168.1.21'], profile(blocked_domains=['bad.example']))))
        self.assertEqual((report['added'], report['deleted']), (1, 1))
        self.assertEqual([c['client'] for c in self.srv.clients], ['192.168.1.21'])
        self.run_sync({'clients': {}, 'keep': set()})
        self.assertEqual(self.srv.clients, [])
        self.assertEqual([d['domain'] for d in self.srv.domains], ['own.example'])       # own rule intact
        self.assertEqual([g['name'] for g in self.srv.groups], ['Default', 'Strict kids'])  # only our own group is gone

    def test_keep_protects_clients_of_groups_without_known_ips(self):
        self.run_sync(desired(Kids=(['192.168.1.20'], profile())))
        self.run_sync({'clients': {}, 'keep': {'PC: Kids'}})
        self.assertEqual(len(self.srv.clients), 1)
        self.assertIn('PC: Kids', [g['name'] for g in self.srv.groups])

    def test_foreign_client_is_never_modified(self):
        self.srv.clients.append({'client': '192.168.1.20', 'comment': 'my laptop', 'groups': [0]})
        _, report = self.run_sync(desired(Kids=(['192.168.1.20'], profile())))
        self.assertEqual(self.srv.clients[0]['comment'], 'my laptop')
        self.assertEqual(self.srv.clients[0]['groups'], [0])
        self.assertTrue(any('already exists in Pi-hole' in w for w in report['warnings']))
        self.run_sync({'clients': {}, 'keep': set()})
        self.assertEqual(len(self.srv.clients), 1)

    def test_warnings_for_unsupported_features_and_missing_groups(self):
        _, report = self.run_sync(desired(Kids=(['10.0.0.2'], profile(
            safesearch=True, categories={'social'}, backend_groups=['Does not exist']))))
        text = ' | '.join(report['warnings'])
        self.assertIn('SafeSearch', text)
        self.assertIn('Does not exist', text)

    def test_auth_failure_and_session_limit(self):
        with self.assertRaises(be.BackendError) as caught:
            be.connect_and_sync(dict(self.settings, password='wrong'), desired())
        self.assertIn('login failed', str(caught.exception))
        for _ in range(5):                                   # sessions do not leak: well over max_sessions
            self.run_sync(desired(Kids=(['10.0.0.2'], profile())))
        self.assertEqual(self.srv.sessions, set())

    def test_connection_test_and_url_normalisation(self):
        self.assertIn('Pi-hole v6.1', be.test_connection(self.settings))
        self.assertIn('Pi-hole v6.1', be.test_connection(dict(self.settings, url=self.srv.url + '/api/')))
        with self.assertRaises(be.BackendError):
            be.test_connection({'backend': 'pihole', 'url': 'ftp://x'})
        with self.assertRaises(be.BackendError):
            be.open_backend({'backend': 'nope', 'url': 'http://x'})

    def test_syncer_works_with_pihole(self):
        logs = []
        syncer = be.Syncer(logs.append)
        syncer.configure(self.settings, desired(Kids=(['192.168.1.20'], profile())))
        self.assertTrue(syncer.sync_once(now=1000))
        self.assertEqual(syncer.get_status()['state'], 'ok')
        self.assertIn('Pi-hole', syncer.get_status()['message'])
        self.assertEqual([c['client'] for c in self.srv.clients], ['192.168.1.20'])

    def test_domain_regex(self):
        self.assertEqual(ph.domain_regex('a-b.example.com'), r'(\.|^)a-b\.example\.com$')


if __name__ == '__main__':
    unittest.main()
