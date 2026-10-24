"""Notifications to the parent: ntfy (JSON publish API) and/or a generic webhook."""
import json
import urllib.request

from pc_i18n import _


def _post(url, payload, headers, timeout=8):
    data = json.dumps(payload).encode()
    hdrs = {'Content-Type': 'application/json'}
    hdrs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hdrs, method='POST')
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read(1024)


def notify(settings, title, message, click=None, event='notification'):
    """
    settings: dict with ntfy_server, ntfy_topic, ntfy_token, webhook_url.
    Returns a list of error messages; empty = everything was sent.
    """
    errors = []
    configured = False
    server = (settings.get('ntfy_server') or '').rstrip('/')
    topic = settings.get('ntfy_topic') or ''
    if server and topic:
        configured = True
        body = {'topic': topic, 'title': title, 'message': message, 'priority': 4,
                'tags': ['hourglass_flowing_sand']}
        if click:
            body['click'] = click
        headers = {}
        if settings.get('ntfy_token'):
            headers['Authorization'] = 'Bearer ' + settings['ntfy_token']
        try:
            _post(server + '/', body, headers)
        except Exception as err:  # netwerk, TLS, HTTP-fouten
            errors.append('ntfy: %s' % err)
    webhook = settings.get('webhook_url') or ''
    if webhook:
        configured = True
        try:
            _post(webhook, {'event': event, 'title': title, 'message': message, 'click': click}, {})
        except Exception as err:
            errors.append('webhook: %s' % err)
    if not configured:
        errors.append(_('No notification channel configured (enter an ntfy server and topic, or a webhook).'))
    return errors
