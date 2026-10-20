#!/usr/local/bin/python3
"""Zet een 'extra tijd'-commando in de wachtrij van parentalcontrold.

Gebruik: addtime.py <groep-uuid> <minuten>
"""
import json
import os
import re
import sys
import time

QUEUE_DIR = '/var/db/parentalcontrol/queue'


def main():
    if len(sys.argv) != 3 or not re.fullmatch(r'[0-9a-fA-F-]{36}', sys.argv[1]):
        print('ERROR: ongeldige groep')
        return 1
    try:
        minutes = int(sys.argv[2])
    except ValueError:
        print('ERROR: ongeldig aantal minuten')
        return 1
    if not 0 < minutes <= 720:
        print('ERROR: minuten moet tussen 1 en 720 liggen')
        return 1
    os.makedirs(QUEUE_DIR, mode=0o700, exist_ok=True)
    name = '%d-%d.json' % (time.time_ns(), os.getpid())
    tmp = os.path.join(QUEUE_DIR, '.' + name)
    with open(tmp, 'w') as handle:
        json.dump({'action': 'add_time', 'group': sys.argv[1].lower(), 'minutes': minutes}, handle)
    os.replace(tmp, os.path.join(QUEUE_DIR, name))
    print('OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
