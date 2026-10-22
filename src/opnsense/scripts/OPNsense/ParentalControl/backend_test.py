#!/usr/local/bin/python3
"""Test de verbinding met de gekozen filter-backend met de opgeslagen instellingen."""
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parentalcontrold import CONFIG_XML, backend_settings, parse_config  # noqa: E402
from pc_backends import test_connection  # noqa: E402
from pc_common import BackendError  # noqa: E402

try:
    cfg = parse_config(ET.parse(CONFIG_XML).getroot())
except (OSError, ET.ParseError) as err:
    print('FOUT: config lezen mislukt: %s' % err)
    sys.exit(0)
settings = backend_settings(cfg)
if settings is None:
    print('FOUT: geen filter-backend gekozen of geen URL ingesteld (sla de instellingen eerst op).')
    sys.exit(0)
try:
    print('OK ' + test_connection(settings))
except BackendError as err:
    print('FOUT: %s' % err)
