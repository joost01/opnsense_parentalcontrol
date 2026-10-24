#!/bin/sh
# For testing on an OPNsense test machine only (as root): copies src/ to /usr/local.
set -e
cd "$(dirname "$0")"
cp -R src/* /usr/local/
chmod +x /usr/local/opnsense/scripts/OPNsense/ParentalControl/*.py \
         /usr/local/opnsense/scripts/OPNsense/ParentalControl/*.sh
rm -f /tmp/opnsense_menu_cache.xml /tmp/opnsense_acl_cache.json
service configd restart
# register the log file with syslog-ng so the "Log File" page works
configctl template reload OPNsense/Syslog >/dev/null 2>&1 || true
configctl syslog restart >/dev/null 2>&1 || true
/usr/local/etc/rc.configure_plugins 2>/dev/null || true
echo "Done. Reload the web interface: Services > Parental Control."
