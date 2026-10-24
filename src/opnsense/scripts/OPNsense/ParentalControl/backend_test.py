#!/usr/local/bin/python3
"""Tests the connection to the selected filter backend with the saved settings."""
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parentalcontrold import CONFIG_XML, backend_settings, parse_config  # noqa: E402
from pc_backends import test_connection  # noqa: E402
from pc_common import BackendError  # noqa: E402
from pc_i18n import _  # noqa: E402

try:
    cfg = parse_config(ET.parse(CONFIG_XML).getroot())
except (OSError, ET.ParseError) as err:
    print('ERROR: %s' % (_('could not read the configuration: %s') % err))
    sys.exit(0)
settings = backend_settings(cfg)
if settings is None:
    print('ERROR: ' + _('no filter backend selected or no URL set (save the settings first).'))
    sys.exit(0)
try:
    print('OK ' + test_connection(settings))
except BackendError as err:
    print('ERROR: %s' % err)
