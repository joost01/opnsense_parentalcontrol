#!/usr/local/bin/python3
"""Prints the status and usage of parentalcontrold as JSON (for the GUI)."""
import json
import time

STATUS_FILE = '/var/run/parentalcontrol/status.json'
USAGE_FILE = '/var/db/parentalcontrol/usage.json'

out = {'running': False, 'groups': {}, 'history': {}, 'backend': None}
try:
    with open(USAGE_FILE) as handle:
        out['history'] = json.load(handle).get('days', {})
except (OSError, ValueError):
    pass
try:
    with open(STATUS_FILE) as handle:
        status = json.load(handle)
    fresh = time.time() - status.get('updated', 0) < 3 * status.get('interval', 10) + 5
    out['running'] = fresh
    out['groups'] = status.get('groups', {})
    out['backend'] = status.get('backend')
    if fresh:
        out['history'] = status.get('history', out['history'])
except (OSError, ValueError):
    pass
print(json.dumps(out))
