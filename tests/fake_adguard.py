"""Small fake AdGuard Home API for tests (clients, services, user rules)."""
import base64
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer


class FakeAdGuard:
    def __init__(self, user='admin', password='secret', services=None, version='v0.107.52'):
        self.user, self.password, self.version = user, password, version
        self.services = services if services is not None else [
            'facebook', 'instagram', 'tiktok', 'youtube', 'netflix', 'steam', 'discord', 'whatsapp']
        self.clients = []
        self.rules = ['||ads.example^', '! my own rule']
        self.calls = []                       # (methode, pad)
        self.service_rules = {'youtube': ['||youtube.com^', '||googlevideo.com^', '||ytimg.com^'],
                              'tiktok': ['||tiktok.com^']}
        self.querylog = []                    # entries, newest first: {'time', 'client', 'question', 'reason'}
        self.page_size = None                 # overrides the 'limit' parameter (to test paging)
        self.fail_next = None                 # HTTP status code the next request receives
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code, payload=None):
                data = json.dumps(payload).encode() if payload is not None else b'OK'
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _auth(self):
                expected = 'Basic ' + base64.b64encode(('%s:%s' % (outer.user, outer.password)).encode()).decode()
                return self.headers.get('Authorization') == expected

            def _handle(self, method):
                length = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                outer.calls.append((method, self.path))
                if not self._auth():
                    return self._send(401, {'error': 'unauthorized'})
                if outer.fail_next:
                    code, outer.fail_next = outer.fail_next, None
                    return self._send(code, {'error': 'boom'})
                path = self.path
                if path == '/control/status':
                    return self._send(200, {'version': outer.version, 'running': True})
                if path == '/control/clients' and method == 'GET':
                    return self._send(200, {'clients': outer.clients or None, 'auto_clients': [], 'supported_tags': []})
                if path == '/control/clients/add':
                    if any(c['name'] == body['name'] for c in outer.clients):
                        return self._send(400, {'error': 'client already exists'})
                    if not body.get('ids'):
                        return self._send(400, {'error': 'id required'})
                    outer.clients.append(body)
                    return self._send(200)
                if path == '/control/clients/update':
                    for i, c in enumerate(outer.clients):
                        if c['name'] == body['name']:
                            outer.clients[i] = body['data']
                            return self._send(200)
                    return self._send(400, {'error': 'client not found'})
                if path == '/control/clients/delete':
                    outer.clients = [c for c in outer.clients if c['name'] != body['name']]
                    return self._send(200)
                if path == '/control/blocked_services/all':
                    return self._send(200, {'blocked_services': [
                        {'id': s, 'name': s.title(), 'rules': outer.service_rules.get(s, [])} for s in outer.services],
                        'groups': []})
                if path.startswith('/control/querylog'):
                    query = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
                    search = query.get('search', [''])[0]
                    older = query.get('older_than', [None])[0]
                    limit = outer.page_size or int(query.get('limit', ['500'])[0])
                    rows = [r for r in outer.querylog if not search or r['client'] == search]
                    if older:
                        rows = [r for r in rows if r['time'] < older]
                    page = rows[:limit]
                    return self._send(200, {'oldest': page[-1]['time'] if len(page) == limit else '', 'data': page})
                if path == '/control/filtering/status':
                    return self._send(200, {'enabled': True, 'user_rules': list(outer.rules)})
                if path == '/control/filtering/set_rules':
                    outer.rules = list(body['rules'])
                    return self._send(200)
                return self._send(404, {'error': 'not found'})

            def do_GET(self):
                self._handle('GET')

            def do_POST(self):
                self._handle('POST')

        self.server = HTTPServer(('127.0.0.1', 0), Handler)
        self.url = 'http://127.0.0.1:%d' % self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def writes(self):
        return [c for c in self.calls if c[0] == 'POST']
