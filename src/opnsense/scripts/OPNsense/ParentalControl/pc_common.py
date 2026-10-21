"""Gedeelde hulpfuncties voor parentalcontrold en de configd-scripts."""
import ipaddress
import re
import subprocess


def run(cmd, stdin=None, timeout=20):
    """Voer een commando uit. Hangt of ontbreekt het, dan volgt een resultaat met returncode 124/127
    in plaats van een blokkerende daemon of een exception."""
    try:
        return subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, 124, '', 'timeout after %ds' % timeout)
    except OSError as err:
        return subprocess.CompletedProcess(cmd, 127, '', str(err))


def norm_mac(mac):
    """ndp/arp laten soms voorloopnullen weg: normaliseer naar aa:bb:cc:dd:ee:ff."""
    try:
        parts = (mac or '').strip().lower().split(':')
        if len(parts) != 6:
            return None
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
    """mac -> set(ip), uit ARP (IPv4) en NDP (IPv6)."""
    result = {}
    for parsed in (parse_arp(run(['/usr/sbin/arp', '-an']).stdout, skip_permanent),
                   parse_ndp(run(['/usr/sbin/ndp', '-an']).stdout, skip_permanent)):
        for mac, ips in parsed.items():
            result.setdefault(mac, set()).update(ips)
    return result


def fmt_dur(sec):
    """Seconden -> '1u 05m' of '12m'."""
    sec = int(round(sec))
    h, m = sec // 3600, (sec % 3600) // 60
    return '%du %02dm' % (h, m) if h else '%dm' % m
