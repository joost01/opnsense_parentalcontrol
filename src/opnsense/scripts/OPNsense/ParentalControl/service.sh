#!/bin/sh
# Start/stop wrapper voor parentalcontrold

PIDFILE=/var/run/parentalcontrold.pid          # pid van de daemon zelf
SUPERVISOR=/var/run/parentalcontrold.supervisor.pid  # pid van daemon(8), herstart de daemon bij een crash
SCRIPT=/usr/local/opnsense/scripts/OPNsense/ParentalControl/parentalcontrold.py
ANCHOR=parentalcontrol

running() {
    [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null
}

case "$1" in
start)
    running && exit 0
    # -r/-R: herstart de daemon na 5 seconden als hij onverwacht stopt
    /usr/sbin/daemon -r -R 5 -f -P "$SUPERVISOR" -p "$PIDFILE" /usr/local/bin/python3 "$SCRIPT"
    ;;
stop)
    # eerst de supervisor stoppen, anders start die de daemon direct opnieuw
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
*)
    echo "usage: $0 start|stop|restart|status|cleanup"
    exit 1
    ;;
esac
exit 0
