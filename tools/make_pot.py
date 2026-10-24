#!/usr/bin/env python3
"""
Extracts all translatable strings of the plugin into a PO template, and optionally merges new strings
into existing PO files (msgstr stays empty for new strings, existing translations are kept).

Sources: Volt views (lang._('...')), PHP (gettext('...')), Python (_('...')), form/model/menu/ACL XML.

Usage:
    tools/make_pot.py                      # writes src/.../lang/parentalcontrol.pot
    tools/make_pot.py --merge              # additionally adds missing strings to every lang/*.po
"""
import ast
import glob
import os
import re
import sys
import xml.etree.ElementTree as ET

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
SRC = os.path.join(ROOT, 'src')
LANG = os.path.join(SRC, 'opnsense', 'scripts', 'OPNsense', 'ParentalControl', 'lang')
VOLT_RE = re.compile(r"lang\._\('((?:[^'\\]|\\.)*)'\)")
PHP_RE = re.compile(r"gettext\('((?:[^'\\]|\\.)*)'\)")


def unescape(text):
    return text.replace("\\'", "'")


def extract():
    found = {}

    def add(msgid, where):
        if msgid and msgid.strip():
            found.setdefault(msgid, []).append(os.path.relpath(where, ROOT))

    for path in sorted(glob.glob(os.path.join(SRC, '**', '*'), recursive=True)):
        if not os.path.isfile(path):
            continue
        if path.endswith('.volt'):
            with open(path) as handle:
                for m in VOLT_RE.finditer(handle.read()):
                    add(unescape(m.group(1)), path)
        elif path.endswith(('.php', '.inc')):
            with open(path) as handle:
                for m in PHP_RE.finditer(handle.read()):
                    add(unescape(m.group(1)), path)
        elif path.endswith('.py'):
            with open(path) as handle:
                tree = ast.parse(handle.read())
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == '_'
                        and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                    add(node.args[0].value, path)
            for name in ('WEEKDAYS',):    # msgids stored in constants and translated when displayed
                for node in ast.walk(tree):
                    if isinstance(node, ast.Assign) and any(getattr(t, 'id', None) == name for t in node.targets):
                        for item in node.value.elts:
                            add(item.value, path)
            for node in ast.walk(tree):    # IGNORED_FEATURES in pc_pihole.py
                if isinstance(node, ast.Assign) and any(getattr(t, 'id', None) == 'IGNORED_FEATURES' for t in node.targets):
                    for item in node.value.elts:
                        add(item.value, path)
        elif path.endswith('.xml'):
            try:
                root = ET.parse(path).getroot()
            except ET.ParseError:
                continue
            for el in root.iter():
                if el.tag in ('label', 'help', 'ValidationMessage') and el.text:
                    add(el.text.strip(), path)
                if el.get('VisibleName'):
                    add(el.get('VisibleName'), path)
            if path.endswith('ParentalControl.xml'):
                for ov in root.iter('OptionValues'):
                    for child in ov:
                        add((child.text or '').strip(), path)
            if path.endswith('ACL.xml'):
                for el in root.iter('name'):
                    add((el.text or '').strip(), path)
    return found


def quote(text):
    return '"%s"' % text.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')


def entry(msgid, msgstr='', refs=()):
    lines = ['#: ' + ' '.join(sorted(set(refs)))] if refs else []
    lines += ['msgid ' + quote(msgid), 'msgstr ' + quote(msgstr), '']
    return '\n'.join(lines)


HEADER = ('msgid ""\nmsgstr ""\n"Project-Id-Version: os-parentalcontrol\\n"\n"MIME-Version: 1.0\\n"\n'
          '"Content-Type: text/plain; charset=UTF-8\\n"\n"Content-Transfer-Encoding: 8bit\\n"\n"Language: %s\\n"\n\n')


def main():
    found = extract()
    os.makedirs(LANG, exist_ok=True)
    with open(os.path.join(LANG, 'parentalcontrol.pot'), 'w', encoding='utf-8') as handle:
        handle.write(HEADER % '' + '\n'.join(entry(m, '', refs) for m, refs in found.items()))
    print('%d strings written to parentalcontrol.pot' % len(found))
    if '--merge' in sys.argv:
        sys.path.insert(0, os.path.join(ROOT, 'src', 'opnsense', 'scripts', 'OPNsense', 'ParentalControl'))
        from pc_i18n import parse_po
        for po in glob.glob(os.path.join(LANG, '*.po')):
            with open(po, encoding='utf-8') as handle:
                existing = parse_po(handle.read())
            missing = [m for m in found if m not in existing]
            if missing:
                with open(po, 'a', encoding='utf-8') as handle:
                    handle.write('\n' + '\n'.join(entry(m, '', found[m]) for m in missing))
            print('%s: %d missing strings added' % (os.path.basename(po), len(missing)))


if __name__ == '__main__':
    main()
