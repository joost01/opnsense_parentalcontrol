#!/usr/local/bin/python3
"""Geeft de verzoeken om extra tijd als JSON (openstaande eerst, dan nieuwste)."""
import json

REQUESTS_FILE = '/var/db/parentalcontrol/requests.json'

try:
    with open(REQUESTS_FILE) as handle:
        items = json.load(handle).get('requests', [])
except (OSError, ValueError):
    items = []
items.sort(key=lambda r: (r.get('status') != 'pending', -r.get('created', 0)))
print(json.dumps(items[:20]))
