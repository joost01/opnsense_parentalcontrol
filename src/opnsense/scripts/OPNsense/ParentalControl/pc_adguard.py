"""
AdGuard Home-koppeling voor websiteprofielen en SafeSearch.

Principes:
  * de plugin beheert uitsluitend AdGuard-clients waarvan de naam met PREFIX begint, en een
    eigen, gemarkeerd blok in de gebruikersregels; alles wat de beheerder zelf instelde blijft
    onaangeroerd;
  * synchronisatie draait in een aparte thread: een trage of onbereikbare AdGuard houdt de
    firewall-daemon nooit op.
"""
import base64
import json
import re
import ssl
import threading
import time
import urllib.error
import urllib.request

PREFIX = 'PC: '
RULES_BEGIN = '! BEGIN parentalcontrol (managed - do not edit)'
RULES_END = '! END parentalcontrol'
MIN_SYNC_INTERVAL = 10       # seconden tussen twee synchronisaties
FULL_SYNC_INTERVAL = 300     # volledige controle, ook zonder wijzigingen
RETRY_INTERVAL = 30          # wachttijd na een fout
SAFE_SEARCH_PROVIDERS = ('bing', 'duckduckgo', 'ecosia', 'google', 'pixabay', 'yandex', 'youtube')

# Categorie -> AdGuard-dienst-ID's. Onbekende ID's (andere AdGuard-versie) worden overgeslagen.
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


class AdGuardError(Exception):
    pass


class AdGuard:
    def __init__(self, url, user='', password='', verify_tls=True, timeout=6):
        url = (url or '').strip().rstrip('/')
        if not re.match(r'^https?://', url):
            raise AdGuardError('ongeldige URL: %r' % url)
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
                raise AdGuardError('inloggen mislukt (HTTP %d): controleer gebruikersnaam en wachtwoord' % err.code)
            raise AdGuardError('HTTP %d op %s: %s' % (err.code, path, detail))
        except (urllib.error.URLError, OSError) as err:
            raise AdGuardError('verbinding mislukt: %s' % getattr(err, 'reason', err))
        try:
            return json.loads(raw) if raw.strip() else None
        except ValueError:
            return raw

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
    """-> (dienst-ID's die bestaan, overgeslagen ID's)."""
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
    """Is de bestaande AdGuard-client al gelijk aan wat we willen? (voorkomt onnodige updates)"""
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
    """Gebruikersregels per client (blokkeren/toestaan van domeinen) in AdGuard-syntaxis."""
    lines = []
    for name in sorted(clients):
        profile = clients[name]['profile']
        for domain in clean_domains(profile['blocked_domains']):
            lines.append("||%s^$client='%s'" % (domain, name))
        for domain in clean_domains(profile['allowed_domains']):
            lines.append("@@||%s^$client='%s'" % (domain, name))
    return lines


def replace_block(rules, new_lines):
    """Vervang ons gemarkeerde blok in de regels; de rest blijft ongewijzigd."""
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
    desired = {'clients': {naam: {'ids': set, 'profile': dict}}, 'keep': set(namen)}
    Geeft een rapport terug; gooit AdGuardError bij communicatieproblemen.
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
        report['warnings'].append('diensten onbekend voor deze AdGuard-versie: ' + ', '.join(sorted(skipped)))
    return report


# ---------------------------------------------------------------- achtergrondthread

class Syncer:
    """Houdt AdGuard in lijn met de gewenste toestand, buiten de hoofdloop van de daemon."""

    def __init__(self, log):
        self.log = log
        self.lock = threading.Lock()
        self.settings = None
        self.desired = None
        self.dirty = False
        self.status = {'state': 'disabled', 'message': '', 'updated': 0, 'warnings': []}
        self.next_try = 0.0
        self.last_sync = 0.0
        self.running = False
        self.thread = None

    def configure(self, settings, desired):
        with self.lock:
            if settings != self.settings or desired != self.desired:
                self.settings, self.desired, self.dirty = settings, desired, True
            if settings is None and self.status['state'] != 'disabled':
                self.status = {'state': 'disabled', 'message': '', 'updated': time.time(), 'warnings': []}
            elif settings is not None and self.status['state'] == 'disabled':
                self.status = {'state': 'pending', 'message': 'wachten op eerste synchronisatie',
                               'updated': time.time(), 'warnings': []}

    def get_status(self):
        with self.lock:
            return dict(self.status)

    def sync_once(self, now=None):
        """Voer zo nodig een synchronisatie uit. Geeft True terug als er een poging is gedaan."""
        now = time.time() if now is None else now
        with self.lock:
            if self.settings is None or self.desired is None:
                return False
            due = self.dirty or now - self.last_sync >= FULL_SYNC_INTERVAL
            if not due or now < self.next_try or now - self.last_sync < MIN_SYNC_INTERVAL:
                return False
            settings, desired = dict(self.settings), self.desired
            self.dirty = False
        report = None
        try:
            api = AdGuard(settings['url'], settings.get('user', ''), settings.get('password', ''),
                          settings.get('verify_tls', True))
            version = api.status().get('version', '?')
            report = sync(api, desired)
            status = {'state': 'ok', 'message': 'AdGuard Home %s' % version, 'updated': time.time(),
                      'warnings': report['warnings']}
            self.next_try = 0.0
        except AdGuardError as err:
            status = {'state': 'error', 'message': str(err), 'updated': time.time(), 'warnings': []}
            self.next_try = now + RETRY_INTERVAL
            with self.lock:
                self.dirty = True                 # opnieuw proberen na de wachttijd
        with self.lock:
            previous = self.status
            self.status, self.last_sync = status, now
        if previous['state'] != status['state'] or (status['state'] == 'error'
                                                    and previous['message'] != status['message']):
            self.log('AdGuard: %s - %s' % (status['state'], status['message']))
        if report and (report['added'] or report['updated'] or report['deleted'] or report['rules']):
            self.log('AdGuard: gesynchroniseerd (+%d ~%d -%d clients, regels %s)' % (
                report['added'], report['updated'], report['deleted'], 'bijgewerkt' if report['rules'] else 'gelijk'))
        for warning in status['warnings']:
            if warning not in previous.get('warnings', []):
                self.log('AdGuard: ' + warning)
        return True

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False

    def _loop(self):
        while self.running:
            try:
                self.sync_once()
            except Exception as err:  # de thread mag nooit sterven
                self.log('AdGuard: onverwachte fout: %r' % err)
            time.sleep(2)
