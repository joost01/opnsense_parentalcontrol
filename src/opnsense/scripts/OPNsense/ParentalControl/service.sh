#!/bin/sh
# Start/stop wrapper for parentalcontrold

PIDFILE=/var/run/parentalcontrold.pid          # pid of the daemon itself
SUPERVISOR=/var/run/parentalcontrold.supervisor.pid  # pid of daemon(8), restarts the daemon after a crash
SCRIPT=/usr/local/opnsense/scripts/OPNsense/ParentalControl/parentalcontrold.py
ANCHOR=parentalcontrol
UNBOUND_FILE=/usr/local/etc/unbound.opnsense.d/parentalcontrol.conf
CLEANUP=/usr/local/opnsense/scripts/OPNsense/ParentalControl/backend_cleanup.py

running() {
    [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null
}

case "$1" in
start)
    running && exit 0
    # -r/-R: restart the daemon after 5 seconds if it stops unexpectedly
    /usr/sbin/daemon -r -R 5 -f -P "$SUPERVISOR" -p "$PIDFILE" /usr/local/bin/python3 "$SCRIPT"
    ;;
stop)
    # stop the supervisor first, otherwise it restarts the daemon immediately
    if [ -f "$SUPERVISOR" ]; then
        kill "$(cat "$SUPERVISOR")" 2>/dev/null
    fi
    if running; then
        kill "$(cat "$PIDFILE")" 2>/dev/null
        for i in 1 2 3 4 5; do
            running || break
            sleep 1
        done
    fi
    rm -f "$PIDFILE" "$SUPERVISOR"
    ;;
restart)
    "$0" stop
    "$0" start
    ;;
status)
    if running; then
        echo "parentalcontrold is running as pid $(cat "$PIDFILE")."
    else
        echo "parentalcontrold is not running."
    fi
    ;;
cleanup)
    /sbin/pfctl -a "$ANCHOR" -F all >/dev/null 2>&1
    ;;
emergency)
    # stop the daemon and release everything the firewall holds (the GUI also disables the plugin)
    "$0" stop
    "$0" cleanup
    ;;
uninstall)
    # used before the package is removed: stop, release, and remove what we created elsewhere
    "$0" emergency
    /usr/local/bin/python3 "$CLEANUP" >/dev/null 2>&1
    if [ -f "$UNBOUND_FILE" ]; then
        rm -f "$UNBOUND_FILE"
        /usr/local/sbin/configctl unbound restart >/dev/null 2>&1
    fi
    ;;
*)
    echo "usage: $0 start|stop|restart|status|cleanup|emergency|uninstall"
    exit 1
    ;;
esac
exit 0
