"""
Blokkeerpagina voor geblokkeerde apparaten.

De server luistert alleen op 127.0.0.1 en ::1. Verkeer van geblokkeerde apparaten naar
poort 80 wordt door pf (rdr) hierheen omgeleid; zo werkt ook de captive-portal-detectie
van Android, iOS en Windows. HTTPS kan niet worden omgeleid zonder certificaatfouten:
dat verkeer wordt geweigerd.
"""
import html
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from pc_common import fmt_dur, norm_ip

MAX_CONNECTIONS = 32   # gelijktijdige verbindingen per server; extra verbindingen worden direct gesloten
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


def render_page(info):
    key = esc(status_key(info))
    if not info.get('blocked'):
        title = 'Je kunt weer verder'
        body = '<h1>Je kunt weer verder</h1><p>De pagina wordt opnieuw geladen...</p>'
        script = 'setTimeout(function(){location.reload()},1500);'
        return _wrap(title, body, script)

    name = esc(info['group_name'])
    if info['reason'] == 'bedtime':
        title = 'Bedtijd'
        until = ' tot %s' % esc(info['until']) if info.get('until') else ''
        body = ('<h1>Het is bedtijd &#127769;</h1><p>Internet voor <b>%s</b> staat uit%s.</p>' % (name, until))
    else:
        title = 'Apparaattijd op'
        used = fmt_dur(info['used'])
        total = fmt_dur((info['limit'] or 0) + info['extra'])
        body = ('<h1>Je apparaattijd is op</h1><p>Vandaag gebruikt door <b>%s</b>: %s van %s.</p>'
                % (name, esc(used), esc(total)))
        body += _request_section(info)

    script = _script(key)
    return _wrap(title, body, script)


def _request_section(info):
    state = info.get('req_state')
    left = int(info.get('cooldown_left', 0))
    if state == 'pending':
        return '<p>&#9203; Je verzoek is verstuurd. Wacht tot een ouder antwoordt...</p>'
    out = ''
    if state == 'denied':
        out += '<p>Je laatste verzoek is afgewezen.</p>'
    elif state == 'approved':
        out += '<p>Je laatste verzoek was goedgekeurd.</p>'
    if info.get('can_request'):
        out += '<p>Wil je extra tijd vragen?</p>'
        for minutes in info['minutes_options']:
            out += '<button type="button" onclick="ask(%d)">%d minuten</button>' % (minutes, minutes)
        out += '<div class="msg" id="msg"></div>'
    elif left > 0:
        out += ('<p class="muted">Je kunt over <span id="cd" data-left="%d">%s</span> opnieuw een verzoek doen.</p>'
                % (left, esc(_mmss(left))))
    elif not info.get('request_enabled', True):
        out += '<p class="muted">Extra tijd vragen is uitgeschakeld.</p>'
    return out


def _mmss(sec):
    return '%d:%02d' % (sec // 60, sec % 60)


def _script(key):
    return """
var KEY="%s";
function poll(){fetch('%s',{cache:'no-store'}).then(function(r){return r.json()}).then(function(s){
if(s.key!==KEY){location.reload();}}).catch(function(){});}
setInterval(poll,4000);
var el=document.getElementById('cd');
if(el){var left=parseInt(el.getAttribute('data-left'),10);
setInterval(function(){left--;if(left<=0){location.reload();return;}
el.textContent=Math.floor(left/60)+':'+('0'+(left%%60)).slice(-2);},1000);}
function ask(m){var msg=document.getElementById('msg');
fetch('%s',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'minutes='+m})
.then(function(r){return r.json()}).then(function(d){if(d.ok){location.reload();}else{msg.textContent=d.message;}})
.catch(function(){msg.textContent='Versturen mislukt, probeer het later opnieuw.';});}
""" % (key, STATUS_PATH, REQUEST_PATH)


def _wrap(title, body, script):
    return ('<!DOCTYPE html><html lang="nl"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>%s</title><style>%s</style></head><body><main>%s</main><script>%s</script></body></html>'
            % (esc(title), CSS, body, script))


def make_handler(daemon):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.0'   # verbinding sluiten na elk antwoord
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

        def _client(self):
            return norm_ip(self.client_address[0]) or self.client_address[0]

        def _serve(self, head_only=False):
            ip = self._client()
            path = self.path.split('?', 1)[0]
            info = daemon.page_info(ip)
            if path == STATUS_PATH:
                self._send(200, 'application/json', json.dumps({'key': status_key(info)}), head_only)
                return
            if not info.get('blocked'):
                # Niet (meer) geblokkeerd: stuur door naar de oorspronkelijke URL.
                host = self.headers.get('Host', '')
                if host and all(c.isalnum() or c in '.-:[]' for c in host):
                    self.send_response(302)
                    self.send_header('Location', 'http://%s%s' % (host, self.path))
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('Content-Length', '0')
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    return
            self._send(200, 'text/html; charset=utf-8', render_page(info), head_only)

        def do_GET(self):
            self._serve()

        def do_HEAD(self):
            self._serve(head_only=True)

        def do_POST(self):
            if self.path.split('?', 1)[0] != REQUEST_PATH:
                self._send(404, 'text/plain; charset=utf-8', 'Niet gevonden')
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
            result = daemon.create_request(self._client(), minutes)
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
            self.shutdown_request(request)   # te druk: voorkomt dat een client threads uitput
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
            log('blokkeerpagina op %s:%d niet gestart: %s' % (addr, port, err))
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
