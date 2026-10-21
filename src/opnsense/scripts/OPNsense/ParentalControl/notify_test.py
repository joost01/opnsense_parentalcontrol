#!/usr/local/bin/python3
"""Verstuurt een testmelding met de opgeslagen instellingen."""
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parentalcontrold import CONFIG_XML, parse_config  # noqa: E402
from pc_notify import notify  # noqa: E402

try:
    settings = parse_config(ET.parse(CONFIG_XML).getroot())['notify']
except (OSError, ET.ParseError) as err:
    print('FOUT: config lezen mislukt: %s' % err)
    sys.exit(0)
errors = notify(settings, 'Ouderlijk toezicht', 'Dit is een testmelding.',
                click=settings.get('gui_url') or None, event='test')
print('OK' if not errors else 'FOUT: ' + ' | '.join(errors))
