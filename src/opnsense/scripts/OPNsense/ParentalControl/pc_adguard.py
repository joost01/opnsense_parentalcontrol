"""
AdGuard Home integration for website profiles and SafeSearch.

Principes:
  * the plugin only manages AdGuard clients whose name starts with PREFIX, and a marked
    block of its own in the user rules; everything the administrator configured stays
    onaangeroerd;
  * synchronisation runs in a separate thread: a slow or unreachable AdGuard never holds up
    the firewall daemon.
"""
import base64
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

from pc_common import BackendError
from pc_i18n import _

PREFIX = 'PC: '
RULES_BEGIN = '! BEGIN parentalcontrol (managed - do not edit)'
RULES_END = '! END parentalcontrol'
SAFE_SEARCH_PROVIDERS = ('bing', 'duckduckgo', 'ecosia', 'google', 'pixabay', 'yandex', 'youtube')

# Category -> AdGuard service IDs. Unknown IDs (other AdGuard version) are skipped.
CATEGORY_SERVICES = {
    'social': ['facebook', 'instagram', 'tiktok', 'snapchat', 'twitter', 'pinterest', 'reddit', 'tumblr',
               'vk', 'mastodon'],
    'messaging': ['whatsapp', 'telegram', 'discord', 'signal', 'viber', 'skype', 'wechat', 'messenger'],
    'games': ['steam', 'epic_games', 'roblox', 'minecraft', 'fortnite', 'blizzard_entertainment',
              'riot_games', 'origin', 'ubisoft'],
    'video': ['youtube', 'netflix', 'disneyplus', 'hbomax', 'hulu', 'twitch', 'amazon_streaming', 'vimeo'],
    'dating': ['tinder'],
}
DOMAIN_RE = re.compile(r'^[a-z0-9]([a-z0-9\-.]{0,251}[a-z0-9])?$')


class AdGuardError(BackendError):
    pass


class AdGuard:
    def __init__(self, url, user='', password='', verify_tls=True, timeout=6):
        url = (url or '').strip().rstrip('/')
        if not re.match(r'^https?://', url):
            raise AdGuardError(_('invalid URL: %r') % url)
        self.url = url
        self.timeout = timeout
        self.headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        if user or password:
            token = base64.b64encode(('%s:%s' % (user, password)).encode()).decode()
            self.headers['Authorization'] = 'Basic ' + token
        self.context = None
        if url.startswith('https') and not verify_tls:
            self.context = ssl._create_unverified_context()

    def _request(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self.context) as resp:
                raw = resp.read().decode('utf-8', 'replace')
        except urllib.error.HTTPError as err:
            detail = err.read(200).decode('utf-8', 'replace').strip()
            if err.code in (401, 403):
                raise AdGuardError(_('login failed (HTTP %d): check the username and password') % err.code)
            raise AdGuardError('HTTP %d op %s: %s' % (err.code, path, detail))
        except (urllib.error.URLError, OSError) as err:
            raise AdGuardError(_('connection failed: %s') % getattr(err, 'reason', err))
        try:
            return json.loads(raw) if raw.strip() else None
        except ValueError:
            return raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def describe(self):
        return 'AdGuard Home %s' % self.status().get('version', '?')

    def test(self):
        version = self.status().get('version', '?')
        services = self.service_ids()
        self.clients()
        self.user_rules()
        return _('AdGuard Home %s (%d blockable services known)') % (version, len(services))

    def status(self):
        data = self._request('GET', '/control/status')
        return data if isinstance(data, dict) else {}

    def clients(self):
        data = self._request('GET', '/control/clients')
        return (data or {}).get('clients') or [] if isinstance(data, dict) else []

    def add_client(self, client):
        self._request('POST', '/control/clients/add', client)

    def update_client(self, name, client):
        self._request('POST', '/control/clients/update', {'name': name, 'data': client})

    def delete_client(self, name):
        self._request('POST', '/control/clients/delete', {'name': name})

    def service_ids(self):
        data = self._request('GET', '/control/blocked_services/all')
        items = data.get('blocked_services') or data.get('services') or [] if isinstance(data, dict) else data
        ids = set()
        for item in items or []:
            ident = item.get('id') if isinstance(item, dict) else item
            if isinstance(ident, str):
                ids.add(ident)
        return ids

    def blocked_services_all(self):
        """The blockable services with their rules (used to recognise apps in the query log)."""
        data = self._request('GET', '/control/blocked_services/all')
        items = data.get('blocked_services') or data.get('services') or [] if isinstance(data, dict) else data
        return [i for i in items or [] if isinstance(i, dict)]

    def querylog(self, search, older_than=None, limit=500):
        query = 'limit=%d&response_status=all&search=%s' % (limit, urllib.parse.quote(search, safe=''))
        if older_than:
            query += '&older_than=' + urllib.parse.quote(older_than, safe='')
        data = self._request('GET', '/control/querylog?' + query)
        return data if isinstance(data, dict) else {}

    def user_rules(self):
        data = self._request('GET', '/control/filtering/status')
        return list((data or {}).get('user_rules') or []) if isinstance(data, dict) else []

    def set_user_rules(self, rules):
        self._request('POST', '/control/filtering/set_rules', {'rules': rules})


# ---------------------------------------------------------------- desired state

def clean_domains(items):
    out = []
    for item in items:
        item = item.strip().lower().lstrip('.')
        if item.startswith('*.'):
            item = item[2:]
        if DOMAIN_RE.match(item) and item not in out:
            out.append(item)
    return out


def services_for(categories, available):
    """-> (service IDs that exist, skipped IDs)."""
    wanted = []
    for cat in sorted(categories):
        for ident in CATEGORY_SERVICES.get(cat, []):
            if ident not in wanted:
                wanted.append(ident)
    known = [i for i in wanted if i in available]
    return sorted(known), [i for i in wanted if i not in available]


def build_client(name, ids, profile, services):
    enabled = bool(profile['safesearch'])
    safe = {'enabled': enabled}
    safe.update({p: True for p in SAFE_SEARCH_PROVIDERS})
    return {
        'name': name,
        'ids': sorted(ids),
        'tags': [],
        'upstreams': [],
        'use_global_settings': False,
        'filtering_enabled': True,
        'safebrowsing_enabled': True,
        'parental_enabled': bool(profile['parental']),
        'safesearch_enabled': enabled,          # AdGuard < 0.107.30
        'safe_search': safe,                    # nieuwere versies
        'use_global_blocked_services': False,
        'blocked_services': services,
    }


def client_matches(existing, wanted):
    """Does the existing AdGuard client already match what we want? (avoids needless updates)"""
    safe = existing.get('safe_search')
    current_safe = safe.get('enabled') if isinstance(safe, dict) else existing.get('safesearch_enabled')
    return (sorted(existing.get('ids') or []) == wanted['ids']
            and sorted(existing.get('blocked_services') or []) == wanted['blocked_services']
            and bool(existing.get('parental_enabled')) == wanted['parental_enabled']
            and bool(current_safe) == wanted['safesearch_enabled']
            and not existing.get('use_global_settings')
            and not existing.get('use_global_blocked_services')
            and bool(existing.get('filtering_enabled')))


def managed_rules(clients):
    """User rules per client (blocking/allowing domains) in AdGuard syntax."""
    lines = []
    for name in sorted(clients):
        profile = clients[name]['profile']
        for domain in clean_domains(profile['blocked_domains']):
            lines.append("||%s^$client='%s'" % (domain, name))
        for domain in clean_domains(profile['allowed_domains']):
            lines.append("@@||%s^$client='%s'" % (domain, name))
    return lines


def replace_block(rules, new_lines):
    """Replace our marked block in the rules; the rest stays unchanged."""
    out, skipping = [], False
    for line in rules:
        if line.strip() == RULES_BEGIN:
            skipping = True
            continue
        if skipping:
            if line.strip() == RULES_END:
                skipping = False
            continue
        out.append(line)
    if new_lines:
        out += [RULES_BEGIN] + new_lines + [RULES_END]
    return out


def sync(api, desired):
    """
    desired = {'clients': {name: {'ids': set, 'profile': dict}}, 'keep': set(names)}
    Returns a report; raises AdGuardError on communication problems.
    """
    available = api.service_ids()
    existing = {c.get('name'): c for c in api.clients() if str(c.get('name', '')).startswith(PREFIX)}
    report = {'added': 0, 'updated': 0, 'deleted': 0, 'rules': False, 'warnings': []}
    skipped = set()
    for name, want in sorted(desired['clients'].items()):
        services, unknown = services_for(want['profile']['categories'], available)
        skipped.update(unknown)
        payload = build_client(name, want['ids'], want['profile'], services)
        if name not in existing:
            api.add_client(payload)
            report['added'] += 1
        elif not client_matches(existing[name], payload):
            api.update_client(name, payload)
            report['updated'] += 1
    for name in sorted(set(existing) - set(desired['clients']) - set(desired.get('keep', ()))):
        api.delete_client(name)
        report['deleted'] += 1
    current = api.user_rules()
    wanted = replace_block(current, managed_rules(desired['clients']))
    if wanted != current:
        api.set_user_rules(wanted)
        report['rules'] = True
    if skipped:
        report['warnings'].append(_('services unknown to this AdGuard version: ') + ', '.join(sorted(skipped)))
    return report
