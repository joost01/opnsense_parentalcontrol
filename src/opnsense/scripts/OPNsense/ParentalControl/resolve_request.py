#!/usr/local/bin/python3
"""Zet een goedkeuring/afwijzing van een verzoek in de wachtrij van parentalcontrold.

Gebruik: resolve_request.py <verzoek-id> approve|deny
"""
import json
import os
import re
import sys
import time

QUEUE_DIR = '/var/db/parentalcontrol/queue'


def main():
    if len(sys.argv) != 3 or not re.fullmatch(r'[0-9a-f]{6,32}', sys.argv[1]) \
            or sys.argv[2] not in ('approve', 'deny'):
        print('ERROR: ongeldige invoer')
        return 1
    os.makedirs(QUEUE_DIR, mode=0o700, exist_ok=True)
    name = '%d-%d.json' % (time.time_ns(), os.getpid())
    tmp = os.path.join(QUEUE_DIR, '.' + name)
    with open(tmp, 'w') as handle:
        json.dump({'action': 'resolve_request', 'id': sys.argv[1], 'approve': sys.argv[2] == 'approve'}, handle)
    os.replace(tmp, os.path.join(QUEUE_DIR, name))
    print('OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
