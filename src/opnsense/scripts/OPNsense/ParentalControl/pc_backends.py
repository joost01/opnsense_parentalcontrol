"""
Filter backend selection and the generic synchronisation thread.

A backend (AdGuard Home, Pi-hole, ...) provides an API object with __enter__/__exit__, describe() and
test(), and a sync(api, desired) function. Everything the administrator configured stays untouched.
"""
import threading
import time

import pc_adguard
import pc_pihole
from pc_common import BackendError
from pc_i18n import _

MIN_SYNC_INTERVAL = 10       # seconden tussen twee synchronisaties
FULL_SYNC_INTERVAL = 300     # full check, also without changes
RETRY_INTERVAL = 30          # wait time after an error

BACKENDS = ('adguard', 'pihole')


def open_backend(settings):
    kind = settings.get('backend')
    if kind == 'adguard':
        api = pc_adguard.AdGuard(settings.get('url'), settings.get('user', ''), settings.get('password', ''),
                                 settings.get('verify_tls', True))
        return api, pc_adguard.sync
    if kind == 'pihole':
        api = pc_pihole.PiHole(settings.get('url'), settings.get('password', ''), settings.get('verify_tls', True))
        return api, pc_pihole.sync
    raise BackendError(_('unknown filter backend: %r') % (kind,))


def connect_and_sync(settings, desired):
    api, sync_fn = open_backend(settings)
    with api:
        label = api.describe()
        return label, sync_fn(api, desired)


def test_connection(settings):
    api, unused = open_backend(settings)
    with api:
        return api.test()


class Syncer:
    """Keeps the backend in line with the desired state, outside the main loop of the daemon."""

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
                self.status = {'state': 'pending', 'message': _('waiting for the first synchronisation'),
                               'updated': time.time(), 'warnings': []}

    def get_status(self):
        with self.lock:
            return dict(self.status)

    def sync_once(self, now=None):
        """Run a synchronisation if needed. Returns True if an attempt was made."""
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
            label, report = connect_and_sync(settings, desired)
            status = {'state': 'ok', 'message': label, 'updated': time.time(), 'warnings': report['warnings']}
            self.next_try = 0.0
        except BackendError as err:
            status = {'state': 'error', 'message': str(err), 'updated': time.time(), 'warnings': []}
            self.next_try = now + RETRY_INTERVAL
            with self.lock:
                self.dirty = True                 # retry after the wait time
        with self.lock:
            previous = self.status
            self.status, self.last_sync = status, now
        if previous['state'] != status['state'] or (status['state'] == 'error'
                                                    and previous['message'] != status['message']):
            self.log('filter backend: %s - %s' % (status['state'], status['message']))
        if report and (report['added'] or report['updated'] or report['deleted'] or report['rules']):
            self.log('filter backend: synchronised (+%d ~%d -%d clients, rules %s)' % (
                report['added'], report['updated'], report['deleted'], 'updated' if report['rules'] else 'unchanged'))
        for warning in status['warnings']:
            if warning not in previous.get('warnings', []):
                self.log('filter backend: ' + warning)
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
            except Exception as err:  # the thread must never die
                self.log('filter backend: unexpected error: %r' % err)
            time.sleep(2)
