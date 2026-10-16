#!/bin/sh
# Start/stop wrapper voor parentalcontrold

PIDFILE=/var/run/parentalcontrold.pid
SCRIPT=/usr/local/opnsense/scripts/OPNsense/ParentalControl/parentalcontrold.py
ANCHOR=parentalcontrol

running() {
    [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null
}

case "$1" in
start)
    running && exit 0
    /usr/sbin/daemon -f -p "$PIDFILE" /usr/local/bin/python3 "$SCRIPT"
    ;;
stop)
    if running; then
        kill "$(cat "$PIDFILE")"
        for i in 1 2 3 4 5; do
            running || break
            sleep 1
        done
    fi
    rm -f "$PIDFILE"
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
*)
    echo "usage: $0 start|stop|restart|status|cleanup"
    exit 1
    ;;
esac
exit 0
