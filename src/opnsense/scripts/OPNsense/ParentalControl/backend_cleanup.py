#!/usr/local/bin/python3
"""Removes everything the plugin created in the filter backend (clients, groups, rules, Unbound file).

Prints 'OK ...' or 'ERROR: ...'. Used by the "remove items" button, when the plugin is disabled and
before the package is removed.
"""
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parentalcontrold import CONFIG_XML, backend_settings, parse_config  # noqa: E402
from pc_backends import cleanup  # noqa: E402
from pc_common import BackendError  # noqa: E402
from pc_i18n import _  # noqa: E402


def main():
    try:
        settings = backend_settings(parse_config(ET.parse(CONFIG_XML).getroot()))
    except (OSError, ET.ParseError) as err:
        print('ERROR: %s' % (_('could not read the configuration: %s') % err))
        return 0
    if settings is None:
        print('OK ' + _('no filter backend is configured, nothing to remove'))
        return 0
    try:
        report = cleanup(settings)
    except BackendError as err:
        print('ERROR: %s' % err)
        return 0
    print('OK ' + _('removed %d client(s); rules %s') % (report['deleted'], _('updated') if report['rules'] else _('unchanged')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
