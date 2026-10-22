"""
Pi-hole (v6) koppeling.

Pi-hole kent per client geen SafeSearch en geen blokkering per dienst. Wat wel kan, en wat deze
koppeling doet, is het beheren van Pi-hole-groepen en clients:
  * per groep met profiel wordt een Pi-hole-groep "PC: <naam>" gemaakt; de apparaten (IP-adressen)
    worden als clients aan die groep (en aan de standaardgroep) gekoppeld;
  * het profiel kan bestaande Pi-hole-groepen aanwijzen (zodat de beheerder in Pi-hole zelf
    bepaalt welke blokkeerlijsten bijvoorbeeld "Kinderen" krijgt);
  * extra geblokkeerde en toegestane domeinen worden als regex-regels aan de groep gekoppeld.
Alleen items die de plugin zelf maakte (opmerking begint met MARK) worden gewijzigd of verwijderd.
"""
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

from pc_common import BackendError

PREFIX = 'PC: '
MARK = 'parentalcontrol: '
DEFAULT_GROUP = 0
IGNORED_FEATURES = ('SafeSearch', 'het volwassenfilter', 'categorieën')


class PiHoleError(BackendError):
    pass


class PiHole:
    def __init__(self, url, password='', verify_tls=True, timeout=6):
        url = (url or '').strip().rstrip('/')
        if not re.match(r'^https?://', url):
            raise PiHoleError('ongeldige URL: %r' % url)
        if url.endswith('/api'):
            url = url[:-4]
        self.base = url + '/api'
        self.password = password or ''
        self.timeout = timeout
        self.sid = None
        self.context = ssl._create_unverified_context() if (url.startswith('https') and not verify_tls) else None

    def _request(self, method, path, body=None, auth=True):
        data = json.dumps(body).encode() if body is not None else None
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        if auth and self.sid:
            headers['sid'] = self.sid
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self.context) as resp:
                raw = resp.read().decode('utf-8', 'replace')
        except urllib.error.HTTPError as err:
            detail = err.read(300).decode('utf-8', 'replace').strip()
            if err.code in (401, 403):
                raise PiHoleError('inloggen mislukt (HTTP %d): controleer het (app-)wachtwoord; '
                                  'tweestapsverificatie wordt niet ondersteund, gebruik een app-wachtwoord' % err.code)
            raise PiHoleError('HTTP %d op %s: %s' % (err.code, path, detail))
        except (urllib.error.URLError, OSError) as err:
            raise PiHoleError('verbinding mislukt: %s' % getattr(err, 'reason', err))
        try:
            return json.loads(raw) if raw.strip() else None
        except ValueError:
            return raw

    # sessies: Pi-hole beperkt het aantal sessies, dus altijd netjes uitloggen
    def __enter__(self):
        data = self._request('POST', '/auth', {'password': self.password}, auth=False)
        session = (data or {}).get('session', {}) if isinstance(data, dict) else {}
        if not session.get('valid') or not session.get('sid'):
            raise PiHoleError('inloggen mislukt: %s' % (session.get('message') or 'geen geldige sessie'))
        self.sid = session['sid']
        return self

    def __exit__(self, *exc):
        if self.sid:
            try:
                self._request('DELETE', '/auth')
            except BackendError:
                pass
            self.sid = None
        return False

    def version(self):
        data = self._request('GET', '/info/version')
        try:
            return data['version']['core']['local']['version']
        except (KeyError, TypeError):
            return '?'

    def describe(self):
        return 'Pi-hole %s' % self.version()

    def test(self):
        groups = self.groups()
        self.clients()
        self.domains()
        return 'Pi-hole %s (%d groepen)' % (self.version(), len(groups))

    def _list(self, path, key):
        data = self._request('GET', path)
        return (data or {}).get(key) or [] if isinstance(data, dict) else []

    def groups(self):
        return self._list('/groups', 'groups')

    def clients(self):
        return self._list('/clients', 'clients')

    def domains(self):
        return self._list('/domains', 'domains')

    def add_group(self, name, comment):
        self._request('POST', '/groups', {'name': name, 'comment': comment, 'enabled': True})

    def delete_group(self, name):
        self._request('DELETE', '/groups/' + urllib.parse.quote(name, safe=''))

    def add_client(self, client, comment, groups):
        self._request('POST', '/clients', {'client': client, 'comment': comment, 'groups': groups})

    def update_client(self, client, comment, groups):
        self._request('PUT', '/clients/' + urllib.parse.quote(client, safe=''),
                      {'comment': comment, 'groups': groups})

    def delete_client(self, client):
        self._request('DELETE', '/clients/' + urllib.parse.quote(client, safe=''))

    def add_domain(self, kind_type, domain, comment, groups):
        self._request('POST', '/domains/%s/regex' % kind_type,
                      {'domain': domain, 'comment': comment, 'groups': groups, 'enabled': True})

    def delete_domain(self, kind_type, domain):
        self._request('DELETE', '/domains/%s/regex/%s' % (kind_type, urllib.parse.quote(domain, safe='')))


def domain_regex(domain):
    """Regex die het domein en alle subdomeinen dekt (domeinen zijn al gevalideerd: a-z, 0-9, '-', '.')."""
    return r'(\.|^)' + domain.replace('.', r'\.') + '$'


def sync(api, desired):
    """Zelfde contract als pc_adguard.sync: desired = {'clients': {...}, 'keep': set}."""
    report = {'added': 0, 'updated': 0, 'deleted': 0, 'rules': False, 'warnings': []}
    clients_wanted = desired['clients']
    keep = set(desired.get('keep', ()))

    groups = {g['name']: g for g in api.groups()}
    created = False
    for name in sorted(clients_wanted):
        if name not in groups:
            api.add_group(name, MARK + 'beheerd door de plugin')
            created = True
    if created:
        groups = {g['name']: g for g in api.groups()}

    missing, ignored = set(), False
    want_clients = {}      # ip -> (opmerking, groep-ID's)
    want_domains = {}      # (type, regex) -> set(groep-ID's)
    for name, want in clients_wanted.items():
        profile = want['profile']
        if profile['safesearch'] or profile['parental'] or profile['categories']:
            ignored = True
        gids = {DEFAULT_GROUP, groups[name]['id']}
        for extra in profile.get('backend_groups', []):
            if extra in groups:
                gids.add(groups[extra]['id'])
            else:
                missing.add(extra)
        for ip in want['ids']:
            want_clients[ip] = (MARK + name, sorted(gids))
        own = groups[name]['id']
        for domain in profile['blocked_domains']:
            want_domains.setdefault(('deny', domain_regex(domain)), set()).add(own)
        for domain in profile['allowed_domains']:
            want_domains.setdefault(('allow', domain_regex(domain)), set()).add(own)

    # clients
    existing = {c['client']: c for c in api.clients()}
    for ip, (comment, gids) in sorted(want_clients.items()):
        current = existing.get(ip)
        if current is None:
            api.add_client(ip, comment, gids)
            report['added'] += 1
        elif not str(current.get('comment', '')).startswith(MARK):
            report['warnings'].append('client %s bestaat al in Pi-hole en wordt niet aangepast' % ip)
        elif sorted(current.get('groups') or []) != gids or current.get('comment') != comment:
            api.update_client(ip, comment, gids)
            report['updated'] += 1
    for ip, current in sorted(existing.items()):
        comment = str(current.get('comment', ''))
        if comment.startswith(MARK) and ip not in want_clients and comment[len(MARK):] not in keep:
            api.delete_client(ip)
            report['deleted'] += 1

    # domeinregels
    existing_domains = {}
    for d in api.domains():
        if str(d.get('comment', '')).startswith(MARK):
            existing_domains[(d.get('type'), d.get('domain'))] = d
    for key, gids in sorted(want_domains.items()):
        current = existing_domains.get(key)
        wanted_groups = sorted(gids)
        if current is not None and sorted(current.get('groups') or []) == wanted_groups \
                and current.get('kind', 'regex') == 'regex':
            continue
        if current is not None:
            api.delete_domain(key[0], key[1])
        api.add_domain(key[0], key[1], MARK, wanted_groups)
        report['rules'] = True
    for key in sorted(set(existing_domains) - set(want_domains)):
        api.delete_domain(key[0], key[1])
        report['rules'] = True

    # beheerde groepen die niet meer nodig zijn (pas na het opruimen van clients en regels)
    for name, group in sorted(groups.items()):
        if (name.startswith(PREFIX) and str(group.get('comment', '')).startswith(MARK)
                and name not in clients_wanted and name not in keep):
            api.delete_group(name)

    if ignored:
        report['warnings'].append('Pi-hole kent per groep geen %s: die instellingen worden genegeerd; '
                                  'koppel in het profiel Pi-hole-groepen met blokkeerlijsten' %
                                  ', '.join(IGNORED_FEATURES))
    if missing:
        report['warnings'].append('Pi-hole-groep(en) bestaan niet: ' + ', '.join(sorted(missing)))
    return report
