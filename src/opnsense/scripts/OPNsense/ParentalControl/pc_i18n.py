"""
Minimal gettext-style translations for text that is rendered outside the OPNsense GUI: the block
page, notifications and messages of the helper scripts. The source language is English; translations
live in lang/<locale>.po (standard PO files, so the usual translation tools work).
The locale is taken from the OPNsense system language (config.xml: system/language).
"""
import os
import re
import time

LANG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lang')
CONFIG_XML = '/conf/config.xml'
CACHE_SECONDS = 60

_state = {'checked': 0.0, 'locale': None, 'catalog': {}, 'forced': None}


def parse_po(text):
    """Parse the msgid/msgstr pairs of a PO file (plural forms and contexts are not used)."""
    catalog, msgid, msgstr, target, fuzzy = {}, None, None, None, False

    def unquote(line):
        body = line.strip()[1:-1]
        return re.sub(r'\\(.)', lambda m: {'n': '\n', 't': '\t'}.get(m.group(1), m.group(1)), body)

    def flush():
        if msgid and msgstr and not fuzzy:
            catalog[msgid] = msgstr

    for line in text.splitlines():
        line = line.strip()
        if line.startswith('#,') and 'fuzzy' in line:
            fuzzy = True
        elif line.startswith('msgid '):
            flush()
            msgid, msgstr, target = unquote(line[6:]), None, 'id'
            fuzzy = fuzzy and False or fuzzy
        elif line.startswith('msgstr '):
            msgstr, target = unquote(line[7:]), 'str'
        elif line.startswith('"') and line.endswith('"'):
            if target == 'id':
                msgid += unquote(line)
            elif target == 'str':
                msgstr += unquote(line)
        elif not line:
            flush()
            msgid, msgstr, target, fuzzy = None, None, None, False
    flush()
    return catalog


def detect_locale():
    try:
        with open(CONFIG_XML) as handle:
            match = re.search(r'<system>.*?<language>([A-Za-z_]+)</language>', handle.read(), re.S)
        return match.group(1) if match else 'en_US'
    except OSError:
        return 'en_US'


def set_locale(locale):
    """Force a locale (used by tests); None restores detection from the system configuration."""
    _state.update({'forced': locale, 'checked': 0.0})


def _load():
    now = time.monotonic()
    if _state['checked'] and now - _state['checked'] < CACHE_SECONDS:
        return _state['catalog']
    locale = _state['forced'] or detect_locale()
    catalog = {}
    for name in (locale, locale.split('_')[0]):
        path = os.path.join(LANG_DIR, name + '.po')
        if os.path.exists(path):
            with open(path, encoding='utf-8') as handle:
                catalog = parse_po(handle.read())
            break
    _state.update({'checked': now, 'locale': locale, 'catalog': catalog})
    return catalog


def _(msgid):
    """Translate msgid; falls back to the English source text."""
    return _load().get(msgid, msgid)


def language():
    """Two-letter language code of the active locale (for the HTML lang attribute)."""
    _load()
    return (_state['locale'] or 'en_US').split('_')[0]
