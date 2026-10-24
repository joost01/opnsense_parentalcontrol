#!/usr/local/bin/python3
"""Queues a day control for parentalcontrold.

Usage: group_cmd.py <group-uuid> block_day|open_day|normal_day|reset_counters
"""
import json
import os
import re
import sys
import time

QUEUE_DIR = '/var/db/parentalcontrol/queue'
COMMANDS = ('block_day', 'open_day', 'normal_day', 'reset_counters')


def main():
    if len(sys.argv) != 3 or not re.fullmatch(r'[0-9a-fA-F-]{36}', sys.argv[1]) or sys.argv[2] not in COMMANDS:
        print('ERROR: invalid input')
        return 1
    os.makedirs(QUEUE_DIR, mode=0o700, exist_ok=True)
    name = '%d-%d.json' % (time.time_ns(), os.getpid())
    tmp = os.path.join(QUEUE_DIR, '.' + name)
    with open(tmp, 'w') as handle:
        json.dump({'action': 'group_cmd', 'group': sys.argv[1].lower(), 'cmd': sys.argv[2]}, handle)
    os.replace(tmp, os.path.join(QUEUE_DIR, name))
    print('OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
