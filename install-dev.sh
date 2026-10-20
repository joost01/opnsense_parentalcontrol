#!/bin/sh
# Alleen voor testen op een OPNsense-testmachine (als root): kopieert src/ naar /usr/local.
set -e
cd "$(dirname "$0")"
cp -R src/* /usr/local/
chmod +x /usr/local/opnsense/scripts/OPNsense/ParentalControl/*.py \
         /usr/local/opnsense/scripts/OPNsense/ParentalControl/*.sh
rm -f /tmp/opnsense_menu_cache.xml /tmp/opnsense_acl_cache.json
service configd restart
/usr/local/etc/rc.configure_plugins 2>/dev/null || true
echo "Klaar. Herlaad de webinterface: Services > Ouderlijk toezicht."
