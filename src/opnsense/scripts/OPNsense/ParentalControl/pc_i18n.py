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
    _catalogs.clear()


_catalogs = {}   # locale -> (time loaded, catalog)

LANGUAGE_NAMES = {'en': 'English', 'nl': 'Nederlands', 'de': 'Deutsch', 'fr': 'Fran\u00e7ais', 'es': 'Espa\u00f1ol',
                  'it': 'Italiano', 'pt': 'Portugu\u00eas', 'pl': 'Polski', 'tr': 'T\u00fcrk\u00e7e', 'da': 'Dansk',
                  'sv': 'Svenska', 'nb': 'Norsk', 'fi': 'Suomi', 'cs': '\u010ce\u0161tina', 'ro': 'Rom\u00e2n\u0103'}


def primary(locale):
    """'nl_NL' / 'nl-NL' / 'NL' -> 'nl'."""
    return (locale or '').replace('-', '_').split('_')[0].lower()


def available_locales():
    """English (the source language) plus every locale that has a PO file."""
    found = []
    try:
        found = sorted(f[:-3] for f in os.listdir(LANG_DIR) if f.endswith('.po'))
    except OSError:
        pass
    return ['en_US'] + [loc for loc in found if primary(loc) != 'en']


def match_locale(code):
    """The available locale for a language code such as 'nl', 'nl-NL' or 'nl_NL'; None if there is none."""
    wanted = primary(code)
    for locale in available_locales():
        if primary(locale) == wanted:
            return locale
    return None


def negotiate(header, default='en_US'):
    """Pick the best available locale for an Accept-Language header; 'default' if nothing matches."""
    choices = []
    for position, part in enumerate((header or '').split(',')):
        tag, _sep, params = part.strip().partition(';')
        quality = 1.0
        match = re.search(r'q\s*=\s*([0-9.]+)', params)
        if match:
            try:
                quality = float(match.group(1))
            except ValueError:
                quality = 0.0
        if tag and tag != '*' and quality > 0:
            choices.append((-quality, position, tag))
    for _q, _pos, tag in sorted(choices):
        locale = match_locale(tag)
        if locale:
            return locale
    return default


def catalog_for(locale):
    now = time.monotonic()
    cached = _catalogs.get(locale)
    if cached and now - cached[0] < CACHE_SECONDS:
        return cached[1]
    catalog = {}
    for name in (locale, primary(locale)):
        path = os.path.join(LANG_DIR, name + '.po')
        if os.path.exists(path):
            with open(path, encoding='utf-8') as handle:
                catalog = parse_po(handle.read())
            break
    _catalogs[locale] = (now, catalog)
    return catalog


def translator(locale):
    """A translate function for one locale (used per request by the block page)."""
    catalog = catalog_for(locale)
    return lambda msgid: catalog.get(msgid, msgid)


def language_choices():
    """[(locale, native name)] of every available language, for a language switcher."""
    return [(loc, LANGUAGE_NAMES.get(primary(loc), primary(loc).upper())) for loc in available_locales()]


def system_locale():
    _load()
    return _state['locale'] or 'en_US'


def _load():
    now = time.monotonic()
    if _state['checked'] and now - _state['checked'] < CACHE_SECONDS:
        return _state['catalog']
    locale = _state['forced'] or detect_locale()
    _state.update({'checked': now, 'locale': locale, 'catalog': catalog_for(locale)})
    return _state['catalog']


def _(msgid):
    """Translate msgid; falls back to the English source text."""
    return _load().get(msgid, msgid)


def language():
    """Two-letter language code of the active locale (for the HTML lang attribute)."""
    _load()
    return (_state['locale'] or 'en_US').split('_')[0]
