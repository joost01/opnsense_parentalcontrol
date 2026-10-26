"""
Block page for blocked devices.

The server only listens on 127.0.0.1 and ::1. Traffic from blocked devices to
port 80 is redirected here by pf (rdr); this also makes the captive portal detection
of Android, iOS and Windows work. HTTPS cannot be redirected without certificate errors:
that traffic is rejected.
"""
import html
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from pc_common import fmt_dur, norm_ip
from pc_i18n import _, negotiate, match_locale, primary, language_choices, system_locale, translator

MAX_CONNECTIONS = 32   # concurrent connections per server; extra connections are closed immediately
STATUS_PATH = '/__pc/status'
REQUEST_PATH = '/__pc/request'

CSS = """
body{margin:0;font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;background:#1f2933;color:#f5f7fa;
display:flex;min-height:100vh;align-items:center;justify-content:center}
main{max-width:30em;margin:1em;padding:2em;background:#323f4b;border-radius:1em;text-align:center}
h1{margin-top:0;font-size:1.6em}p{line-height:1.5}.muted{color:#9aa5b1;font-size:.9em}
button{font-size:1em;margin:.3em;padding:.7em 1.2em;border:0;border-radius:.6em;background:#2680c2;color:#fff;cursor:pointer}
button:disabled{background:#52606d;cursor:default}.msg{margin-top:1em;min-height:1.5em}
"""


def esc(value):
    return html.escape(str(value), quote=True)


def status_key(info):
    return '%s|%s|%s|%s' % (info.get('blocked'), info.get('reason'), info.get('can_request'),
                            info.get('req_state'))


def render_page(info, tr=None, locale=None, choices=()):
    """Render the page in the language of tr/locale (default: the system language)."""
    tr = tr or _
    locale = locale or system_locale()
    key = esc(status_key(info))
    if not info.get('blocked'):
        title = tr('You can continue')
        body = '<h1>%s</h1><p>%s</p>' % (esc(title), esc(tr('The page is being reloaded...')))
        return _wrap(title, body, 'setTimeout(function(){location.reload()},1500);', locale)

    name = esc(info['group_name'])
    reason = info['reason']
    if reason == 'bedtime':
        title = tr('Bedtime')
        until = esc(tr(' until %s') % info['until']) if info.get('until') else ''
        body = '<h1>%s &#127769;</h1><p>%s</p>' % (
            esc(tr('It is bedtime')), tr('Internet access for <b>%s</b> is switched off%s.') % (name, until))
    elif reason == 'manual':
        title = tr('Internet switched off')
        body = '<h1>%s</h1><p>%s</p>' % (
            esc(title), tr('A parent switched off internet access for <b>%s</b> for today.') % name)
    else:
        title = tr('Device time is up')
        used = fmt_dur(info['used'], tr)
        total = fmt_dur((info['limit'] or 0) + info['extra'], tr)
        body = '<h1>%s</h1><p>%s</p>' % (
            esc(tr('Your device time is up')),
            tr('Used today by <b>%s</b>: %s of %s.') % (name, esc(used), esc(total)))
        body += _request_section(info, tr)
    body += _switcher(choices, locale)
    return _wrap(title, body, _script(key, tr, locale), locale)


def _request_section(info, tr):
    state = info.get('req_state')
    left = int(info.get('cooldown_left', 0))
    if state == 'pending':
        return '<p>&#9203; %s</p>' % esc(tr('Your request has been sent. Please wait for a parent to answer...'))
    out = ''
    if state == 'denied':
        out += '<p>%s</p>' % esc(tr('Your last request was rejected.'))
    elif state == 'approved':
        out += '<p>%s</p>' % esc(tr('Your last request was approved.'))
    if info.get('can_request'):
        out += '<p>%s</p>' % esc(tr('Would you like to ask for extra time?'))
        for minutes in info['minutes_options']:
            out += '<button type="button" onclick="ask(%d)">%s</button>' % (minutes, esc(tr('%d minutes') % minutes))
        out += '<div class="msg" id="msg"></div>'
    elif left > 0:
        countdown = '<span id="cd" data-left="%d">%s</span>' % (left, esc(_mmss(left)))
        out += '<p class="muted">%s</p>' % (tr('You can make a new request in %s.') % countdown)
    elif not info.get('request_enabled', True):
        out += '<p class="muted">%s</p>' % esc(tr('Asking for extra time is disabled.'))
    return out


def _mmss(sec):
    return '%d:%02d' % (sec // 60, sec % 60)


def _script(key, tr, locale):
    return """
var KEY="%s";var LANG=%s;
function poll(){fetch('%s',{cache:'no-store'}).then(function(r){return r.json()}).then(function(s){
if(s.key!==KEY){location.reload();}}).catch(function(){});}
setInterval(poll,4000);
var el=document.getElementById('cd');
if(el){var left=parseInt(el.getAttribute('data-left'),10);
setInterval(function(){left--;if(left<=0){location.reload();return;}
el.textContent=Math.floor(left/60)+':'+('0'+(left%%60)).slice(-2);},1000);}
function ask(m){var msg=document.getElementById('msg');
fetch('%s',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'minutes='+m+'&lang='+encodeURIComponent(LANG)})
.then(function(r){return r.json()}).then(function(d){if(d.ok){location.reload();}else{msg.textContent=d.message;}})
.catch(function(){msg.textContent=%s;});}
""" % (key, json.dumps(locale), STATUS_PATH, REQUEST_PATH,
           json.dumps(tr('Sending failed, please try again later.')))


def _wrap(title, body, script, locale):
    return ('<!DOCTYPE html><html lang="%s"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>%s</title><style>%s</style></head><body><main>%s</main><script>%s</script></body></html>'
            % (esc(primary(locale)), esc(title), CSS, body, script))


def _switcher(choices, current):
    """Small language links under the message (only when there is more than one language)."""
    if len(choices) < 2:
        return ''
    links = ' &middot; '.join(
        '<b>%s</b>' % esc(name) if loc == current else '<a href="?pc_lang=%s">%s</a>' % (esc(primary(loc)), esc(name))
        for loc, name in choices)
    return '<p class="muted" style="margin-top:1.5em">%s</p>' % links


def make_handler(daemon):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.0'   # close the connection after every response
        server_version = 'ParentalControl'
        timeout = 10

        def log_message(self, *args):
            pass

        def _send(self, code, content_type, payload, head_only=False):
            data = payload.encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy',
                             "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                             "connect-src 'self'; frame-ancestors 'none'")
            self.send_header('Connection', 'close')
            self.end_headers()
            if not head_only:
                self.wfile.write(data)

        def _locale(self, requested=None):
            """?pc_lang=xx (the switcher) > Accept-Language of the browser > system language."""
            if requested is None:
                requested = (parse_qs(urlparse(self.path).query).get('pc_lang') or [''])[0]
            chosen = match_locale(requested) if requested else None
            return chosen or negotiate(self.headers.get('Accept-Language', ''), system_locale())

        def _client(self):
            return norm_ip(self.client_address[0]) or self.client_address[0]

        def _serve(self, head_only=False):
            ip = self._client()
            path = self.path.split('?', 1)[0]
            locale = self._locale()
            tr = translator(locale)
            info = daemon.page_info(ip, tr)
            if path == STATUS_PATH:
                self._send(200, 'application/json', json.dumps({'key': status_key(info)}), head_only)
                return
            if not info.get('blocked'):
                # No longer blocked: redirect to the original URL.
                host = self.headers.get('Host', '')
                if host and all(c.isalnum() or c in '.-:[]' for c in host):
                    self.send_response(302)
                    self.send_header('Location', 'http://%s%s' % (host, self.path))
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('Content-Length', '0')
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    return
            self._send(200, 'text/html; charset=utf-8', render_page(info, tr, locale, language_choices()), head_only)

        def do_GET(self):
            self._serve()

        def do_HEAD(self):
            self._serve(head_only=True)

        def do_POST(self):
            if self.path.split('?', 1)[0] != REQUEST_PATH:
                self._send(404, 'text/plain; charset=utf-8', 'Not found')
                return
            try:
                length = min(int(self.headers.get('Content-Length', 0)), 1024)
            except ValueError:
                length = 0
            fields = parse_qs(self.rfile.read(length).decode('utf-8', 'replace'))
            try:
                minutes = int(fields.get('minutes', ['0'])[0])
            except ValueError:
                minutes = 0
            locale = self._locale(fields.get('lang', [''])[0])
            result = daemon.create_request(self._client(), minutes, translator(locale))
            self._send(200, 'application/json', json.dumps(result))

    return Handler


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64

    def __init__(self, *args, **kwargs):
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)   # too busy: prevents a client from exhausting threads
            return
        super().process_request(request, client_address)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class _Server6(_Server):
    address_family = socket.AF_INET6

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        super().server_bind()


def start(daemon, port, log):
    servers = []
    for cls, addr in ((_Server, '127.0.0.1'), (_Server6, '::1')):
        try:
            server = cls((addr, port), make_handler(daemon))
        except OSError as err:
            log('block page on %s:%d not started: %s' % (addr, port, err))
            continue
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
    return servers


def stop(servers):
    for server in servers:
        try:
            server.shutdown()
            server.server_close()
        except OSError:
            pass
