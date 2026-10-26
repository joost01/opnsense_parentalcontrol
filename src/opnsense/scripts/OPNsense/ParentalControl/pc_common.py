"""Shared helpers for parentalcontrold and the configd scripts."""
import ipaddress
import re
import subprocess

from pc_i18n import _


def run(cmd, stdin=None, timeout=20):
    """Run a command. If it hangs or is missing, a result with return code 124/127 is returned
    instead of a blocked daemon or an exception."""
    try:
        return subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, '', 'timeout after %ds' % timeout)
    except OSError as err:
        return subprocess.CompletedProcess(cmd, 127, '', str(err))


def norm_mac(mac):
    """Normalise to aa:bb:cc:dd:ee:ff. Accepts aa:bb:.. , aa-bb-.. , aabb.ccdd.eeff,
    aabbccddeeff and the notation without leading zeros used by ndp/arp."""
    text = (mac or '').strip().lower()
    digits = None
    if re.fullmatch(r'[0-9a-f]{12}', text):
        digits = text
    elif re.fullmatch(r'([0-9a-f]{4}\.){2}[0-9a-f]{4}', text):
        digits = text.replace('.', '')
    if digits:
        return ':'.join(digits[i:i + 2] for i in range(0, 12, 2))
    parts = re.split(r'[:-]', text)
    if len(parts) != 6 or any(not 1 <= len(p) <= 2 for p in parts):
        return None
    try:
        return ':'.join('%02x' % int(p, 16) for p in parts)
    except ValueError:
        return None


def norm_ip(text):
    try:
        return str(ipaddress.ip_address(text.strip().split('%')[0]))
    except ValueError:
        return None


def parse_arp(output, skip_permanent=False):
    result = {}
    for line in output.splitlines():
        if skip_permanent and 'permanent' in line:
            continue
        m = re.match(r'\S+ \(([0-9.]+)\) at ([0-9a-fA-F:]+) on', line)
        if m:
            mac, ip = norm_mac(m.group(2)), norm_ip(m.group(1))
            if mac and ip:
                result.setdefault(mac, set()).add(ip)
    return result


def parse_ndp(output, skip_permanent=False):
    result = {}
    for line in output.splitlines():
        if skip_permanent and 'permanent' in line:
            continue
        m = re.match(r'([0-9a-fA-F:]+)(?:%\S+)?\s+([0-9a-fA-F:]+)\s+\S+', line)
        if not m:
            continue
        mac, ip = norm_mac(m.group(2)), norm_ip(m.group(1))
        if not mac or not ip:
            continue
        addr = ipaddress.ip_address(ip)
        if addr.is_link_local or addr.is_multicast:
            continue
        result.setdefault(mac, set()).add(ip)
    return result


def neighbours(skip_permanent=False):
    """mac -> set(ip), from ARP (IPv4) and NDP (IPv6)."""
    result = {}
    for parsed in (parse_arp(run(['/usr/sbin/arp', '-an']).stdout, skip_permanent),
                   parse_ndp(run(['/usr/sbin/ndp', '-an']).stdout, skip_permanent)):
        for mac, ips in parsed.items():
            result.setdefault(mac, set()).update(ips)
    return result


def fmt_dur(sec, tr=None):
    """Seconds -> '1h 05m' or '12m' (the unit letters are translatable)."""
    sec = int(round(sec))
    h, m = sec // 3600, (sec % 3600) // 60
    tr = tr or _
    return (tr('%dh %02dm') % (h, m)) if h else (tr('%dm') % m)


class BackendError(Exception):
    """Communication or configuration error with a filter backend (AdGuard Home, Pi-hole, ...)."""
