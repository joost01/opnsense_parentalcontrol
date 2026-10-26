"""
Statistics for one group: usage per day, block history, recent activity and, when the filter backend
keeps a query log (AdGuard Home), the most used apps and most visited sites.
"""
import json
import re
import time
from datetime import datetime, timedelta

PERIODS = {'day': 1, 'week': 7, 'month': 30}
BLOCKED_REASONS = ('FilteredBlackList', 'FilteredSafeBrowsing', 'FilteredParental', 'FilteredInvalid',
                   'FilteredBlockedService')
TWO_LEVEL_SUFFIXES = {'co.uk', 'org.uk', 'ac.uk', 'com.au', 'net.au', 'co.nz', 'co.jp', 'com.br', 'co.za', 'com.tr'}
PIHOLE_BLOCKED_PREFIXES = ('GRAVITY', 'REGEX', 'DENYLIST', 'EXTERNAL_BLOCKED')
MAX_ENTRIES = 5000
QUERY_BUDGET_SECONDS = 12


def period_start(period, now=None):
    """Start of the period as a timestamp: 'day' = since midnight, 'week' = 7 days, 'month' = 30 days."""
    now = now or datetime.now()
    days = PERIODS.get(period, 1)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    return start.timestamp()


def usage_series(usage_days, uuid, since):
    """[(date, seconds)] for every day of the period that has data, oldest first."""
    first = datetime.fromtimestamp(since).date().isoformat()
    return [(day, round(entry.get(uuid, 0.0))) for day, entry in sorted(usage_days.items())
            if day >= first and entry.get(uuid, 0.0) > 0]


def read_events(path, uuid, since):
    events = []
    try:
        with open(path) as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if record.get('group') == uuid and record.get('ts', 0) >= since:
                    events.append(record)
    except OSError:
        pass
    return sorted(events, key=lambda r: r['ts'])


def block_periods(events, now=None):
    """Pair 'blocked' and 'released' events into [{'start', 'end' (None = still blocked), 'reason'}]."""
    now_ts = (now or time.time())
    periods, current = [], None
    for event in events:
        if event['kind'] == 'blocked':
            if current:
                current['end'] = event['ts']
                periods.append(current)
            current = {'start': event['ts'], 'end': None, 'reason': event.get('reason')}
        elif event['kind'] == 'released' and current:
            current['end'] = event['ts']
            periods.append(current)
            current = None
    if current:
        periods.append(current)
    return [dict(p, duration=round((p['end'] or now_ts) - p['start'])) for p in periods]


def registrable(domain):
    labels = domain.lower().rstrip('.').split('.')
    if len(labels) <= 2:
        return '.'.join(labels)
    if '.'.join(labels[-2:]) in TWO_LEVEL_SUFFIXES:
        return '.'.join(labels[-3:])
    return '.'.join(labels[-2:])


def service_domains(services):
    """{domain: service name} from the rules of AdGuard's blockable services (||example.com^)."""
    mapping = {}
    for service in services:
        for rule in service.get('rules') or []:
            match = re.match(r'^\|\|([a-z0-9.\-]+)\^?$', rule.strip(), re.I)
            if match and '.' in match.group(1):
                mapping.setdefault(match.group(1).lower(), service.get('name') or service.get('id'))
    return mapping


def app_for(domain, mapping):
    labels = domain.lower().rstrip('.').split('.')
    for i in range(len(labels) - 1):
        name = mapping.get('.'.join(labels[i:]))
        if name:
            return name
    return None


def parse_time(text):
    """Parse an RFC 3339 timestamp with up to nanoseconds into a Unix timestamp."""
    text = re.sub(r'(\.\d{6})\d+', r'\1', text.replace('Z', '+00:00'))
    return datetime.fromisoformat(text).timestamp()


def fetch_queries(api, ips, since, budget=QUERY_BUDGET_SECONDS, max_entries=MAX_ENTRIES):
    """Query log entries (newest first) of the given client addresses; returns (entries, truncated)."""
    entries, truncated, deadline = [], False, time.monotonic() + budget
    for ip in ips:
        older_than = None
        while True:
            if time.monotonic() > deadline or len(entries) >= max_entries:
                truncated = True
                break
            page = api.querylog(ip, older_than)
            data = page.get('data') or []
            for row in data:
                ts = parse_time(row['time'])
                if ts < since:
                    data = []
                    break
                entries.append({'ts': ts, 'domain': row['question']['name'].rstrip('.').lower(),
                                'blocked': row.get('reason') in BLOCKED_REASONS})
            older_than = page.get('oldest')
            if not data or not older_than:
                break
    entries.sort(key=lambda e: -e['ts'])
    if len(entries) > max_entries:
        entries, truncated = entries[:max_entries], True
    return entries, truncated


def aggregate(entries, mapping, top=10):
    apps, sites = {}, {}
    for entry in entries:
        app = app_for(entry['domain'], mapping)
        if app:
            apps[app] = apps.get(app, 0) + 1
        site = sites.setdefault(registrable(entry['domain']), {'count': 0, 'blocked': 0})
        site['count'] += 1
        site['blocked'] += 1 if entry['blocked'] else 0
    top_apps = sorted(apps.items(), key=lambda kv: -kv[1])[:top]
    top_sites = sorted(sites.items(), key=lambda kv: -kv[1]['count'])[:top]
    return ([{'name': n, 'count': c} for n, c in top_apps],
            [{'domain': d, 'count': v['count'], 'blocked': v['blocked']} for d, v in top_sites])


def fetch_queries_pihole(api, ips, since, until=None, budget=QUERY_BUDGET_SECONDS, max_entries=MAX_ENTRIES):
    """Same as fetch_queries, for the Pi-hole v6 query log; returns (entries, truncated)."""
    entries, truncated, deadline = [], False, time.monotonic() + budget
    until = until or time.time()
    for ip in ips:
        cursor = None
        while True:
            if time.monotonic() > deadline or len(entries) >= max_entries:
                truncated = True
                break
            page = api.queries(ip, since, until, cursor)
            rows = page.get('queries') or []
            for row in rows:
                entries.append({'ts': float(row['time']), 'domain': str(row['domain']).rstrip('.').lower(),
                                'blocked': str(row.get('status', '')).startswith(PIHOLE_BLOCKED_PREFIXES)})
            new_cursor = page.get('cursor')
            if not rows or not new_cursor or new_cursor == cursor:
                break
            cursor = new_cursor
    entries.sort(key=lambda e: -e['ts'])
    if len(entries) > max_entries:
        entries, truncated = entries[:max_entries], True
    return entries, truncated
