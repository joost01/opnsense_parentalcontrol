"""Unbound backend, built-in catalogue, Pi-hole statistics and backend cleanup."""
import json
import os
import tempfile
import time
import unittest
from types import SimpleNamespace

from helpers import G1, load, make_daemon, xml_config  # sets up sys.path
from fake_adguard import FakeAdGuard
from fake_pihole import FakePiHole

import pc_backends  # noqa: E402
import pc_catalog  # noqa: E402
import pc_unbound  # noqa: E402

st = load('pc_stats_extra_test', 'pc_stats.py')
ph = load('pc_pihole_extra_test', 'pc_pihole.py')


def profile(**kw):
    base = {'safesearch': False, 'parental': False, 'categories': set(), 'blocked_domains': [],
            'allowed_domains': [], 'backend_groups': []}
    base.update(kw)
    return base


def desired(**clients):
    return {'clients': {pc_unbound.PREFIX + n: {'ids': set(ids), 'profile': prof} for n, (ids, prof) in clients.items()},
            'keep': set()}


class CatalogTests(unittest.TestCase):
    def test_categories_reference_known_apps_and_domains_are_unique(self):
        owner = {}
        for app, domains in pc_catalog.APPS.items():
            for domain in domains:
                self.assertRegex(domain, r'^[a-z0-9][a-z0-9.\-]*\.[a-z]{2,}$')
                self.assertNotIn(domain, owner, '%s belongs to %s and %s' % (domain, owner.get(domain), app))
                owner[domain] = app
        for category, apps in pc_catalog.CATEGORIES.items():
            for app in apps:
                self.assertIn(app, pc_catalog.APPS, category)

    def test_helpers(self):
        self.assertIn('tinder.com', pc_catalog.category_domains(['dating']))
        self.assertEqual(pc_catalog.domain_to_app()['googlevideo.com'], 'YouTube')
        self.assertTrue(pc_catalog.covered('www.youtube.com', {'youtube.com'}))
        self.assertTrue(pc_catalog.covered('youtube.com', {'youtube.com'}))
        self.assertFalse(pc_catalog.covered('notyoutube.com', {'youtube.com'}))
        self.assertFalse(pc_catalog.covered('example.com', {'com'}))     # a bare TLD never matches
        self.assertEqual(pc_catalog.SAFESEARCH['www.google.nl'], 'forcesafesearch.google.com')


class UnboundRenderTests(unittest.TestCase):
    def test_view_with_safesearch_categories_and_domains(self):
        text, warnings = pc_unbound.render(desired(
            Kids=(['192.168.1.20', '2001:db8::5'], profile(safesearch=True, categories={'social'},
                  blocked_domains=['bad.example'], allowed_domains=['facebook.com']))))
        self.assertTrue(text.startswith(pc_unbound.HEADER + 'server:'))
        self.assertIn('access-control-view: 192.168.1.20/32 pc_kids', text)
        self.assertIn('access-control-view: 2001:db8::5/128 pc_kids', text)
        self.assertIn('view:\n    name: "pc_kids"\n    view-first: yes', text)
        self.assertIn('local-zone: "tiktok.com." always_nxdomain', text)
        self.assertIn('local-zone: "bad.example." always_nxdomain', text)
        self.assertIn('local-zone: "facebook.com." transparent', text)           # allowed overrides the category
        self.assertNotIn('local-zone: "facebook.com." always_nxdomain', text)
        self.assertIn('local-data: "www.google.com. 300 IN CNAME forcesafesearch.google.com."', text)
        self.assertEqual(warnings, [])

    def test_safesearch_does_not_undo_a_block(self):
        text, _ = pc_unbound.render(desired(Kids=(['10.0.0.2'], profile(safesearch=True, categories={'video'}))))
        self.assertIn('local-zone: "youtube.com." always_nxdomain', text)
        self.assertNotIn('www.youtube.com. 300 IN CNAME', text)
        self.assertIn('www.bing.com. 300 IN CNAME strict.bing.com.', text)

    def test_warnings_and_unique_view_names(self):
        text, warnings = pc_unbound.render(desired(**{
            'A b': (['10.0.0.2'], profile(parental=True)),
            'A_b': (['10.0.0.3'], profile(backend_groups=['x']))}))
        self.assertIn('pc_a_b\n', text)
        self.assertIn('pc_a_b_2', text)
        self.assertEqual(len(warnings), 2)

    def test_empty_desired_renders_nothing(self):
        self.assertEqual(pc_unbound.render({'clients': {}, 'keep': set()}), ('', []))


class FakeRunner:
    def __init__(self):
        self.calls, self.check_ok = [], True

    def __call__(self, cmd, stdin=None, timeout=20):
        self.calls.append(cmd[1:])
        if cmd[1:] == ['unbound', 'check']:
            return SimpleNamespace(stdout='no errors in /var/unbound/unbound.conf' if self.check_ok
                                   else '[1] unbound-checkconf: error in line 3', stderr='', returncode=0 if self.check_ok else 1)
        return SimpleNamespace(stdout='', stderr='', returncode=0)


def read(path):
    with open(path) as handle:
        return handle.read()


class UnboundSyncTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.runner = FakeRunner()
        self.api = pc_unbound.UnboundLocal(self.dir, self.runner)
        self.want = desired(Kids=(['10.0.0.2'], profile(categories={'dating'})))

    def restarts(self):
        return self.runner.calls.count(['unbound', 'restart'])

    def test_write_validate_restart_then_idempotent(self):
        report = pc_unbound.sync(self.api, self.want)
        self.assertEqual((report['added'], report['rules']), (1, True))
        self.assertIn('tinder.com', read(self.api.path))
        self.assertEqual((self.runner.calls[0:2]), [['unbound', 'check'], ['unbound', 'restart']])
        report = pc_unbound.sync(self.api, self.want)
        self.assertEqual((report['updated'], report['rules']), (0, False))
        self.assertEqual(self.restarts(), 1, 'no restart without a change')

    def test_update_and_removal(self):
        pc_unbound.sync(self.api, self.want)
        report = pc_unbound.sync(self.api, desired(Kids=(['10.0.0.2', '10.0.0.9'], profile(categories={'dating'}))))
        self.assertEqual(report['updated'], 1)
        report = pc_unbound.sync(self.api, {'clients': {}, 'keep': set()})
        self.assertEqual(report['deleted'], 1)
        self.assertFalse(os.path.exists(self.api.path))
        self.assertEqual(self.restarts(), 3)

    def test_keep_protects_the_file_when_addresses_are_unknown(self):
        pc_unbound.sync(self.api, self.want)
        pc_unbound.sync(self.api, {'clients': {}, 'keep': {'PC: Kids'}})
        self.assertTrue(os.path.exists(self.api.path))

    def test_invalid_configuration_is_rolled_back(self):
        pc_unbound.sync(self.api, self.want)
        good = read(self.api.path)
        self.runner.check_ok = False
        with self.assertRaises(pc_backends.BackendError) as caught:
            pc_unbound.sync(self.api, desired(Kids=(['10.0.0.7'], profile())))
        self.assertIn('rejected', str(caught.exception))
        self.assertEqual(read(self.api.path), good)
        self.assertEqual(self.restarts(), 1)
        fresh = pc_unbound.UnboundLocal(tempfile.mkdtemp(), self.runner)
        with self.assertRaises(pc_backends.BackendError):
            pc_unbound.sync(fresh, self.want)
        self.assertFalse(os.path.exists(fresh.path), 'a rejected first file is removed again')

    def test_connection_test(self):
        self.assertIn('writable and valid', self.api.test())
        self.runner.check_ok = False
        with self.assertRaises(pc_backends.BackendError):
            self.api.test()

    def test_backend_registry_and_settings(self):
        self.assertIn('unbound', pc_backends.BACKENDS)
        ctx = make_daemon(xml_config(general='<filter_backend>unbound</filter_backend>'))
        settings = ctx.mod.backend_settings(ctx.d.cfg)
        self.assertEqual(settings['backend'], 'unbound')                       # no URL needed
        ctx2 = make_daemon(xml_config(general='<filter_backend>pihole</filter_backend>'))
        self.assertIsNone(ctx2.mod.backend_settings(ctx2.d.cfg))               # a URL is needed
        api, _ = pc_backends.open_backend({'backend': 'unbound'})
        self.assertIsInstance(api, pc_unbound.UnboundLocal)

    def test_cleanup_removes_the_unbound_file(self):
        pc_unbound.CONF_DIR, pc_unbound.run = self.dir, self.runner
        try:
            pc_unbound.sync(self.api, self.want)
            report = pc_backends.cleanup({'backend': 'unbound'})
        finally:
            pc_unbound.CONF_DIR, pc_unbound.run = '/usr/local/etc/unbound.opnsense.d', __import__('pc_common').run
        self.assertEqual(report['deleted'], 1)
        self.assertFalse(os.path.exists(self.api.path))


class CleanupTests(unittest.TestCase):
    def test_adguard_cleanup_removes_only_plugin_items(self):
        srv = FakeAdGuard()
        self.addCleanup(srv.close)
        srv.clients.append({'name': 'Mine', 'ids': ['10.0.0.99'], 'use_global_settings': True})
        settings = {'backend': 'adguard', 'url': srv.url, 'user': 'admin', 'password': 'secret'}
        want = {'clients': {'PC: Kids': {'ids': {'10.0.0.2'}, 'profile': profile(blocked_domains=['bad.example'])}}, 'keep': set()}
        pc_backends.connect_and_sync(settings, want)
        self.assertEqual(len(srv.clients), 2)
        report = pc_backends.cleanup(settings)
        self.assertEqual([c['name'] for c in srv.clients], ['Mine'])
        self.assertEqual((report['deleted'], report['rules']), (1, True))
        self.assertEqual(srv.rules, ['||ads.example^', '! my own rule'])

    def test_pihole_cleanup(self):
        srv = FakePiHole()
        self.addCleanup(srv.close)
        settings = {'backend': 'pihole', 'url': srv.url, 'password': 'apppw'}
        want = {'clients': {'PC: Kids': {'ids': {'10.0.0.2'}, 'profile': profile(blocked_domains=['bad.example'])}}, 'keep': set()}
        pc_backends.connect_and_sync(settings, want)
        pc_backends.cleanup(settings)
        self.assertEqual(srv.clients, [])
        self.assertEqual([d['domain'] for d in srv.domains], ['own.example'])
        self.assertEqual([g['name'] for g in srv.groups], ['Default', 'Strict kids'])


class PiHoleStatsTests(unittest.TestCase):
    def setUp(self):
        self.srv = FakePiHole()
        self.addCleanup(self.srv.close)
        now = time.time()
        rows = [(1, '10.0.0.2', 'www.youtube.com', 'FORWARDED'), (2, '10.0.0.3', 'ads.tracker.net', 'GRAVITY'),
                (3, '10.0.0.2', 'r1.googlevideo.com', 'CACHE'), (4, '10.0.0.9', 'other.example', 'FORWARDED'),
                (5, '10.0.0.2', 'x.tiktok.com', 'REGEX'), (6, '10.0.0.2', 'old.example', 'FORWARDED')]
        self.srv.queries = [{'id': 100 - i, 'time': now - 60 * i if i < 6 else now - 7200, 'domain': d, 'status': s,
                             'client': {'ip': ip}} for i, ip, d, s in rows]
        self.since = now - 3600

    def test_fetch_pages_filters_and_flags_blocked(self):
        with ph.PiHole(self.srv.url, 'apppw') as api:
            entries, truncated = st.fetch_queries_pihole(api, ['10.0.0.2', '10.0.0.3'], self.since)
            api.queries  # the page size of the fake is the 'length' parameter: force paging below
            entries2, _ = st.fetch_queries_pihole(SimpleNamespace(
                queries=lambda ip, since, until, cursor=None: api.queries(ip, since, until, cursor, length=1)),
                ['10.0.0.2'], self.since)
        self.assertFalse(truncated)
        self.assertEqual(sorted(e['domain'] for e in entries),
                         ['ads.tracker.net', 'r1.googlevideo.com', 'www.youtube.com', 'x.tiktok.com'])
        self.assertEqual(len(entries2), 3, 'paging with a cursor collects every row of the client')
        blocked = {e['domain']: e['blocked'] for e in entries}
        self.assertTrue(blocked['ads.tracker.net'] and blocked['x.tiktok.com'])
        self.assertFalse(blocked['www.youtube.com'] or blocked['r1.googlevideo.com'])

    def test_apps_come_from_the_builtin_catalogue(self):
        with ph.PiHole(self.srv.url, 'apppw') as api:
            entries, _ = st.fetch_queries_pihole(api, ['10.0.0.2'], self.since)
        apps, sites = st.aggregate(entries, pc_catalog.domain_to_app())
        self.assertEqual({a['name']: a['count'] for a in apps}, {'YouTube': 2, 'TikTok': 1})

    def test_group_stats_uses_pihole_when_selected(self):
        gs = load('group_stats_pihole_test', 'group_stats.py')
        tmp = tempfile.mkdtemp()
        cfg, status = os.path.join(tmp, 'config.xml'), os.path.join(tmp, 'status.json')
        with open(cfg, 'w') as handle:
            handle.write('<opnsense><OPNsense><ParentalControl><general><enabled>1</enabled>'
                         '<filter_backend>pihole</filter_backend><backend_url>%s</backend_url>'
                         '<backend_password>apppw</backend_password></general></ParentalControl></OPNsense></opnsense>' % self.srv.url)
        with open(status, 'w') as handle:
            json.dump({'groups': {G1: {'ips': ['10.0.0.2']}}}, handle)
        gs.CONFIG_XML, gs.STATUS_FILE = cfg, status
        result = gs.query_part(G1, self.since)
        self.assertTrue(result['queries_available'])
        self.assertEqual({a['name'] for a in result['apps']}, {'YouTube', 'TikTok'})
        self.assertEqual(self.srv.sessions, set())


if __name__ == '__main__':
    unittest.main()
