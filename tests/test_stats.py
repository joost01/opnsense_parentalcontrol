import json
import os
import tempfile
import unittest
from datetime import datetime

from fake_adguard import FakeAdGuard
from helpers import G1, load

st = load('pc_stats_test', 'pc_stats.py')
ag = load('pc_adguard_stats_test', 'pc_adguard.py')

NOW = datetime(2026, 10, 21, 15, 0, 0)


def ts(text):
    return datetime.fromisoformat(text).timestamp()


class PureFunctionTests(unittest.TestCase):
    def test_period_start(self):
        self.assertEqual(datetime.fromtimestamp(st.period_start('day', NOW)).date().isoformat(), '2026-10-21')
        self.assertEqual(datetime.fromtimestamp(st.period_start('week', NOW)).date().isoformat(), '2026-10-15')
        self.assertEqual(datetime.fromtimestamp(st.period_start('month', NOW)).date().isoformat(), '2026-09-22')

    def test_usage_series_filters_by_period_and_group(self):
        days = {'2026-10-14': {G1: 100}, '2026-10-16': {G1: 600.4}, '2026-10-17': {'other': 5}, '2026-10-18': {G1: 0}}
        since = st.period_start('week', NOW)
        self.assertEqual(st.usage_series(days, G1, since), [('2026-10-16', 600)])

    def test_block_periods_pair_events(self):
        events = [{'ts': 100, 'kind': 'blocked', 'reason': 'bedtime'}, {'ts': 400, 'kind': 'released'},
                  {'ts': 500, 'kind': 'blocked', 'reason': 'exhausted'}, {'ts': 600, 'kind': 'request'},
                  {'ts': 700, 'kind': 'blocked', 'reason': 'manual'}]
        periods = st.block_periods(events, now=1000)
        self.assertEqual([(p['reason'], p['start'], p['end'], p['duration']) for p in periods],
                         [('bedtime', 100, 400, 300), ('exhausted', 500, 700, 200), ('manual', 700, None, 300)])

    def test_registrable_domain_and_apps(self):
        self.assertEqual(st.registrable('r1---sn.googlevideo.com'), 'googlevideo.com')
        self.assertEqual(st.registrable('www.bbc.co.uk'), 'bbc.co.uk')
        self.assertEqual(st.registrable('example.com'), 'example.com')
        mapping = st.service_domains([{'id': 'youtube', 'name': 'YouTube', 'rules': ['||youtube.com^', '|http://x^']},
                                      {'id': 'tiktok', 'name': 'TikTok', 'rules': ['||tiktok.com^']}])
        self.assertEqual(st.app_for('www.youtube.com', mapping), 'YouTube')
        self.assertEqual(st.app_for('v16.tiktok.com', mapping), 'TikTok')
        self.assertIsNone(st.app_for('example.org', mapping))

    def test_parse_time_with_nanoseconds_and_zone(self):
        self.assertEqual(st.parse_time('2026-10-21T10:11:12.123456789+02:00'), ts('2026-10-21T10:11:12.123456+02:00'))
        self.assertEqual(st.parse_time('2026-10-21T10:11:12Z'), ts('2026-10-21T10:11:12+00:00'))

    def test_read_events_filters_group_and_since(self):
        path = os.path.join(tempfile.mkdtemp(), 'events.jsonl')
        with open(path, 'w') as handle:
            for record in ({'ts': 10, 'group': G1, 'kind': 'a'}, {'ts': 200, 'group': G1, 'kind': 'b'},
                           {'ts': 300, 'group': 'x', 'kind': 'c'}):
                handle.write(json.dumps(record) + '\n')
            handle.write('not json\n')
        self.assertEqual([e['kind'] for e in st.read_events(path, G1, 100)], ['b'])
        self.assertEqual(st.read_events('/nonexistent', G1, 0), [])


class QueryLogTests(unittest.TestCase):
    def setUp(self):
        self.srv = FakeAdGuard()
        self.addCleanup(self.srv.close)
        self.api = ag.AdGuard(self.srv.url, 'admin', 'secret')
        base = datetime.now().replace(microsecond=0)
        self.t = lambda minutes: (base.timestamp() - minutes * 60)
        self.iso = lambda minutes: datetime.fromtimestamp(self.t(minutes)).astimezone().isoformat()

        def row(minutes, client, name, reason='NotFilteredNotFound'):
            return {'time': self.iso(minutes), 'client': client, 'question': {'name': name + '.'}, 'reason': reason}
        self.srv.querylog = [
            row(1, '10.0.0.2', 'www.youtube.com'), row(2, '10.0.0.3', 'a.tiktok.com', 'FilteredBlockedService'),
            row(3, '10.0.0.2', 'r1.googlevideo.com'), row(4, '10.0.0.9', 'other.example'),
            row(5, '10.0.0.2', 'ads.tracker.net', 'FilteredBlackList'), row(90, '10.0.0.2', 'old.example')]

    def test_fetch_filters_clients_and_period_with_paging(self):
        self.srv.page_size = 2
        entries, truncated = st.fetch_queries(self.api, ['10.0.0.2', '10.0.0.3'], self.t(60))
        self.assertFalse(truncated)
        self.assertEqual(sorted(e['domain'] for e in entries),
                         ['a.tiktok.com', 'ads.tracker.net', 'r1.googlevideo.com', 'www.youtube.com'])
        self.assertTrue([e['ts'] for e in entries] == sorted((e['ts'] for e in entries), reverse=True))
        self.assertTrue(next(e for e in entries if e['domain'] == 'a.tiktok.com')['blocked'])
        self.assertFalse(next(e for e in entries if e['domain'] == 'www.youtube.com')['blocked'])

    def test_entry_limit_marks_truncated(self):
        entries, truncated = st.fetch_queries(self.api, ['10.0.0.2'], self.t(600), max_entries=2)
        self.assertTrue(truncated)

    def test_aggregate_apps_and_sites(self):
        entries, _ = st.fetch_queries(self.api, ['10.0.0.2', '10.0.0.3'], self.t(60))
        mapping = st.service_domains(self.api.blocked_services_all())
        apps, sites = st.aggregate(entries, mapping)
        self.assertEqual({a['name']: a['count'] for a in apps}, {'Youtube': 2, 'Tiktok': 1})
        by_domain = {s['domain']: s for s in sites}
        self.assertEqual(by_domain['googlevideo.com']['count'], 1)
        self.assertEqual(by_domain['tracker.net'], {'domain': 'tracker.net', 'count': 1, 'blocked': 1})


class GroupStatsScriptTests(unittest.TestCase):
    def test_script_without_backend_reports_a_note(self):
        gs = load('group_stats_test', 'group_stats.py')
        tmp = tempfile.mkdtemp()
        cfg = os.path.join(tmp, 'config.xml')
        with open(cfg, 'w') as handle:
            handle.write('<opnsense><OPNsense><ParentalControl><general><enabled>1</enabled></general>'
                         '</ParentalControl></OPNsense></opnsense>')
        gs.CONFIG_XML = cfg
        result = gs.query_part(G1, 0)
        self.assertFalse(result['queries_available'])
        self.assertIn('No filter backend', result['queries_note'])


if __name__ == '__main__':
    unittest.main()
