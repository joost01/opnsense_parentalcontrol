import importlib.util
import os
import re
import tempfile
import unittest

from helpers import ROOT_DIR, SCRIPTS, load

import pc_i18n as i18n   # the same module instance the daemon code imports
web = load('pc_web_i18n_test', 'pc_web.py')
LANG_DIR = os.path.join(SCRIPTS, 'lang')


def make_pot_module():
    spec = importlib.util.spec_from_file_location('make_pot', os.path.join(ROOT_DIR, 'tools', 'make_pot.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def placeholders(text):
    return sorted(re.findall(r'%[sdr]|%02d', text))


class PoParserTests(unittest.TestCase):
    def test_parse_multiline_escapes_and_fuzzy(self):
        po = ('msgid ""\nmsgstr "Language: nl\\n"\n\n'
              'msgid "Hello"\nmsgstr "Hallo"\n\n'
              'msgid "Two "\n"lines"\nmsgstr "Twee "\n"regels"\n\n'
              'msgid "Quote \\"x\\""\nmsgstr "Aanhaling \\"x\\"\\n"\n\n'
              '#, fuzzy\nmsgid "Fuzzy"\nmsgstr "Onzeker"\n\n'
              'msgid "Empty"\nmsgstr ""\n')
        catalog = i18n.parse_po(po)
        self.assertEqual(catalog['Hello'], 'Hallo')
        self.assertEqual(catalog['Two lines'], 'Twee regels')
        self.assertEqual(catalog['Quote "x"'], 'Aanhaling "x"\n')
        self.assertNotIn('Fuzzy', catalog)
        self.assertNotIn('Empty', catalog)

    def test_locale_detection_and_fallback(self):
        tmp = tempfile.mkdtemp()
        cfg = os.path.join(tmp, 'config.xml')
        with open(cfg, 'w') as handle:
            handle.write('<opnsense><system><hostname>fw</hostname><language>nl_NL</language></system></opnsense>')
        i18n.CONFIG_XML = cfg
        self.assertEqual(i18n.detect_locale(), 'nl_NL')
        i18n.CONFIG_XML = os.path.join(tmp, 'missing.xml')
        self.assertEqual(i18n.detect_locale(), 'en_US')
        i18n.set_locale('xx_XX')                                  # unknown language: English source text
        self.assertEqual(i18n._('Request sent.'), 'Request sent.')
        i18n.set_locale(None)


class TranslationTests(unittest.TestCase):
    def tearDown(self):
        i18n.set_locale(None)

    def test_block_page_is_translated(self):
        info = {'blocked': True, 'reason': 'exhausted', 'group_name': 'Kids', 'used': 3600, 'limit': 3600, 'extra': 0,
                'can_request': True, 'req_state': 'none', 'cooldown_left': 0, 'minutes_options': (15, 30, 60),
                'request_enabled': True}
        i18n.set_locale('en_US')
        english = web.render_page(info)
        self.assertIn('Your device time is up', english)
        self.assertIn('15 minutes', english)
        self.assertIn('<html lang="en">', english)
        i18n.set_locale('nl_NL')
        dutch = web.render_page(info)
        self.assertIn('Je apparaattijd is op', dutch)
        self.assertIn('15 minuten', dutch)
        self.assertIn('Vandaag gebruikt door <b>Kids</b>: 1u 00m van 1u 00m.', dutch)
        self.assertIn('<html lang="nl">', dutch)

    def test_manual_and_bedtime_pages_in_dutch(self):
        i18n.set_locale('nl_NL')
        base = {'blocked': True, 'group_name': 'Kids', 'used': 0, 'limit': None, 'extra': 0, 'can_request': False,
                'req_state': 'none', 'cooldown_left': 0, 'minutes_options': (), 'request_enabled': True}
        self.assertIn('Een ouder heeft internet', web.render_page(dict(base, reason='manual')))
        self.assertIn('tot 07:00', web.render_page(dict(base, reason='bedtime', until='07:00')))

    def test_duration_units_follow_the_language(self):
        pc_common = load('pc_common_i18n_test', 'pc_common.py')
        i18n.set_locale('en_US')
        self.assertEqual(pc_common.fmt_dur(3900), '1h 05m')
        i18n.set_locale('nl_NL')
        self.assertEqual(pc_common.fmt_dur(3900), '1u 05m')


class CatalogCompletenessTests(unittest.TestCase):
    """Every translatable string in the code must be in the template and in each translation."""

    @classmethod
    def setUpClass(cls):
        cls.found = make_pot_module().extract()

    def test_template_is_up_to_date(self):
        with open(os.path.join(LANG_DIR, 'parentalcontrol.pot'), encoding='utf-8') as handle:
            text = handle.read()
        ids = {m.replace('\\"', '"').replace('\\\\', '\\') for m in re.findall(r'^msgid "(.*)"$', text, re.M)[1:]}
        self.assertEqual(ids, set(self.found), 'run tools/make_pot.py --merge')

    def test_every_translation_is_complete_with_matching_placeholders(self):
        for path in sorted(os.path.join(LANG_DIR, f) for f in os.listdir(LANG_DIR) if f.endswith('.po')):
            with open(path, encoding='utf-8') as handle:
                catalog = i18n.parse_po(handle.read())
            name = os.path.basename(path)
            missing = [m for m in self.found if m not in catalog]
            self.assertEqual(missing, [], '%s misses %d strings' % (name, len(missing)))
            for msgid, msgstr in catalog.items():
                if msgid in self.found:
                    self.assertEqual(placeholders(msgid), placeholders(msgstr), '%s: %r' % (name, msgid))

    def test_no_dutch_left_in_english_sources(self):
        dutch = re.compile(r"\b(geblokkeerd|bedtijd|apparaten|verzoek|ouderlijk|groepen|instellingen|toepassen)\b", re.I)
        for msgid in self.found:
            self.assertIsNone(dutch.search(msgid), 'Dutch msgid: %r' % msgid)


if __name__ == '__main__':
    unittest.main()
