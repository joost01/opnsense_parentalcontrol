#!/usr/local/bin/python3
"""Test de verbinding met AdGuard Home met de opgeslagen instellingen."""
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parentalcontrold import CONFIG_XML, parse_config  # noqa: E402
from pc_adguard import AdGuard, AdGuardError  # noqa: E402

try:
    cfg = parse_config(ET.parse(CONFIG_XML).getroot())
except (OSError, ET.ParseError) as err:
    print('FOUT: config lezen mislukt: %s' % err)
    sys.exit(0)
if not cfg['adguard_url']:
    print('FOUT: geen AdGuard-URL ingesteld (sla de instellingen eerst op).')
    sys.exit(0)
try:
    api = AdGuard(cfg['adguard_url'], cfg['adguard_user'], cfg['adguard_password'], cfg['adguard_verify_tls'])
    version = api.status().get('version', '?')
    services = api.service_ids()
    api.clients()
    api.user_rules()
    print('OK AdGuard Home %s (%d blokkeerbare diensten bekend)' % (version, len(services)))
except AdGuardError as err:
    print('FOUT: %s' % err)
