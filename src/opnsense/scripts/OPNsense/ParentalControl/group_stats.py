#!/usr/local/bin/python3
"""Prints statistics for one group as JSON (for the group detail panel).

Usage: group_stats.py <group-uuid> day|week|month
"""
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pc_catalog  # noqa: E402
import pc_stats  # noqa: E402
from parentalcontrold import CONFIG_XML, EVENTS_FILE, STATUS_FILE, USAGE_FILE, backend_settings, parse_config  # noqa: E402
from pc_backends import open_backend  # noqa: E402
from pc_common import BackendError  # noqa: E402
from pc_i18n import _  # noqa: E402


def load(path, default):
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def query_part(uuid, since):
    """Apps, sites and recent queries from the filter backend's query log, if it has one."""
    result = {'queries_available': False, 'queries_note': '', 'apps': [], 'sites': [], 'queries': [],
              'truncated': False}
    try:
        cfg = parse_config(ET.parse(CONFIG_XML).getroot())
    except (OSError, ET.ParseError):
        return result
    settings = backend_settings(cfg)
    if not settings:
        result['queries_note'] = _('No filter backend is configured.')
        return result
    if settings['backend'] not in ('adguard', 'pihole'):
        result['queries_note'] = _('The query log is only available with AdGuard Home or Pi-hole.')
        return result
    ips = load(STATUS_FILE, {}).get('groups', {}).get(uuid, {}).get('ips', [])
    if not ips:
        result['queries_note'] = _('No device of this group has a known address right now.')
        return result
    try:
        api, _sync = open_backend(settings)
        with api:
            if settings['backend'] == 'adguard':
                entries, truncated = pc_stats.fetch_queries(api, ips, since)
                mapping = pc_stats.service_domains(api.blocked_services_all()) or pc_catalog.domain_to_app()
            else:
                entries, truncated = pc_stats.fetch_queries_pihole(api, ips, since)
                mapping = pc_catalog.domain_to_app()
        result['apps'], result['sites'] = pc_stats.aggregate(entries, mapping)
        result.update(queries_available=True, truncated=truncated, queries=entries[:50])
    except (BackendError, KeyError, ValueError) as err:
        result['queries_note'] = _('The query log could not be read: %s') % err
    return result


def main():
    if len(sys.argv) != 3 or not re.fullmatch(r'[0-9a-fA-F-]{36}', sys.argv[1]) or sys.argv[2] not in pc_stats.PERIODS:
        print(json.dumps({'error': 'invalid input'}))
        return 1
    uuid, period = sys.argv[1].lower(), sys.argv[2]
    since = pc_stats.period_start(period)
    usage = load(USAGE_FILE, {}).get('days', {})
    events = pc_stats.read_events(EVENTS_FILE, uuid, since)
    out = {'period': period, 'since': since, 'events': events[-100:],
           'usage': pc_stats.usage_series(usage, uuid, since),
           'blocks': pc_stats.block_periods(events)}
    out['total'] = sum(seconds for _day, seconds in out['usage'])
    out.update(query_part(uuid, since))
    print(json.dumps(out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
