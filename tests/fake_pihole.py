"""Fake Pi-hole v6 API for tests (sessions, groups, clients, domains)."""
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer


class FakePiHole:
    def __init__(self, password='apppw', max_sessions=2):
        self.password, self.max_sessions = password, max_sessions
        self.sessions = set()
        self.logins = self.logouts = 0
        self.groups = [{'name': 'Default', 'id': 0, 'enabled': True, 'comment': ''},
                       {'name': 'Strict kids', 'id': 7, 'enabled': True, 'comment': 'by the administrator'}]
        self.clients = []
        self.domains = [{'domain': 'own.example', 'type': 'deny', 'kind': 'exact', 'comment': 'my own rule',
                         'groups': [0], 'enabled': True}]
        self.next_id = 10
        self.queries = []                    # newest first: {'id', 'time', 'domain', 'status', 'client': {'ip'}}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code, payload=None):
                data = json.dumps(payload).encode() if payload is not None else b''
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _handle(self, method):
                length = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                path = urllib.parse.unquote(self.path)
                if path == '/api/auth' and method == 'POST':
                    if body.get('password') != outer.password:
                        return self._send(401, {'session': {'valid': False, 'message': 'password incorrect'}})
                    if len(outer.sessions) >= outer.max_sessions:
                        return self._send(429, {'error': 'too many sessions'})
                    sid = 'sid%d' % outer.logins
                    outer.logins += 1
                    outer.sessions.add(sid)
                    return self._send(200, {'session': {'valid': True, 'sid': sid, 'validity': 300}})
                sid = self.headers.get('sid')
                if sid not in outer.sessions:
                    return self._send(401, {'error': 'unauthorized'})
                if path == '/api/auth' and method == 'DELETE':
                    outer.sessions.discard(sid)
                    outer.logouts += 1
                    return self._send(204)
                if path.startswith('/api/queries'):
                    q = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
                    ip = q.get('client_ip', [''])[0]
                    since, until = float(q['from'][0]), float(q['until'][0])
                    length = int(q.get('length', ['100'])[0])
                    cursor = int(q['cursor'][0]) if 'cursor' in q else None
                    rows = [r for r in outer.queries if (not ip or r['client']['ip'] == ip)
                            and since <= r['time'] <= until and (cursor is None or r['id'] < cursor)]
                    page = rows[:length]
                    return self._send(200, {'queries': page, 'cursor': page[-1]['id'] if len(page) == length else None})
                if path == '/api/info/version':
                    return self._send(200, {'version': {'core': {'local': {'version': 'v6.1'}}}})
                if path == '/api/groups':
                    if method == 'GET':
                        return self._send(200, {'groups': outer.groups})
                    outer.groups.append({'name': body['name'], 'id': outer.next_id, 'enabled': body['enabled'],
                                         'comment': body['comment']})
                    outer.next_id += 1
                    return self._send(201, {})
                if path.startswith('/api/groups/') and method == 'DELETE':
                    outer.groups = [g for g in outer.groups if g['name'] != path[len('/api/groups/'):]]
                    return self._send(204)
                if path == '/api/clients':
                    if method == 'GET':
                        return self._send(200, {'clients': outer.clients})
                    if any(c['client'] == body['client'] for c in outer.clients):
                        return self._send(400, {'error': {'key': 'database_error', 'message': 'UNIQUE constraint failed'}})
                    outer.clients.append(dict(body))
                    return self._send(201, {})
                if path.startswith('/api/clients/'):
                    name = path[len('/api/clients/'):]
                    if method == 'PUT':
                        for c in outer.clients:
                            if c['client'] == name:
                                c.update(body)
                                return self._send(200, {})
                        return self._send(404, {})
                    if method == 'DELETE':
                        outer.clients = [c for c in outer.clients if c['client'] != name]
                        return self._send(204)
                if path == '/api/domains' and method == 'GET':
                    return self._send(200, {'domains': outer.domains})
                if path.startswith('/api/domains/'):
                    parts = path[len('/api/domains/'):].split('/', 2)
                    if method == 'POST':
                        outer.domains.append({'domain': body['domain'], 'type': parts[0], 'kind': parts[1],
                                              'comment': body['comment'], 'groups': body['groups'], 'enabled': True})
                        return self._send(201, {})
                    if method == 'DELETE':
                        outer.domains = [d for d in outer.domains if not (
                            d['type'] == parts[0] and d['kind'] == parts[1] and d['domain'] == parts[2])]
                        return self._send(204)
                return self._send(404, {'error': 'not found'})

            def do_GET(self):
                self._handle('GET')

            def do_POST(self):
                self._handle('POST')

            def do_PUT(self):
                self._handle('PUT')

            def do_DELETE(self):
                self._handle('DELETE')

        self.server = HTTPServer(('127.0.0.1', 0), Handler)
        self.url = 'http://127.0.0.1:%d' % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
