# os-parentalcontrol

Parental control for OPNsense: device groups, bedtime schedules, daily time budgets, requests for extra
time, DNS enforcement and website profiles with SafeSearch.

> **Status: early development version.** Not yet tested on every OPNsense version. Try it on a test machine
> (VM) first. Blocking features can lock devices out of the network.

## Features

| Feature | Status |
|---|---|
| Groups with one or more devices (by MAC address), also selectable from DHCP reservations, leases and ARP/NDP | done |
| Flexible bedtime schedules per day/week, one schedule for several groups | done |
| Daily device time per group (simultaneous use counts once), per weekday, one rule for several groups | done |
| Block a group for the rest of the day, open it for the whole day, reset the counters of today | done |
| Block page ("time is up" / bedtime / blocked by a parent) with requests for extra time (one per period), approval in the GUI, notification through ntfy or webhook | done |
| DNS enforcement: all port 53 traffic (also hard-coded 8.8.8.8 and the like) is sent to your DNS filter; DoT and well-known DoH resolvers are blocked | done |
| Website profiles per age: SafeSearch, adult filter, categories, own domains | done (AdGuard Home) |
| Filter backends: AdGuard Home (full), Pi-hole v6 (groups and own domains, experimental) | done |
| Overview with a detail panel per group: time per day, most used apps, most visited sites, block history, recent activity (day/week/month) | done (apps and sites need the AdGuard Home query log) |
| Website profiles through Unbound (for people without an external DNS filter) | planned |

## Pages

Under **Services > Parental Control**:

* **Overview**: status per group, day controls, requests, and details per group (click a group).
* **Administration**: General, Groups, Devices, Bedtimes, Time budgets and Website profiles.
* **Log File**: the log of the daemon (standard OPNsense log viewer).

## How it works

* The plugin registers a pf anchor `parentalcontrol` (filter, rdr and nat). The daemon `parentalcontrold`
  (Python) loads the rules and one pf table per group into it, so the firewall does not have to be
  reloaded for every block. Existing connections are broken when an address is blocked (`pfctl -k`).
* Devices are identified by MAC address; the daemon maps them to the current IPv4/IPv6 addresses through
  ARP/NDP.
* Time counting: every tick the elapsed time is added once per group if at least one device had more traffic
  than the activity threshold (bytes delta over the pf states). Short pauses count thanks to a grace period.
* Block page: for blocked devices pf redirects DNS and HTTP (port 80) to a small web server in the daemon
  that only listens on 127.0.0.1/::1. This also triggers the captive portal detection of Android, iOS and
  Windows. Everything else is rejected. If pf cannot load the redirect rules, the daemon falls back to
  blocking only, and logs why.
* Requests for extra time: when the time is up, a child can ask for 15/30/60 minutes (at most one request per
  configurable period). The parent approves or rejects in the OPNsense GUI (behind the login) and is notified
  through ntfy or a webhook.
* DNS enforcement: on the selected interfaces (never WAN!) all DNS traffic is redirected to the first DNS
  server you enter; that server itself is excluded. If it is in the same subnet as the clients, the daemon adds
  a nat rule automatically so the replies come back through the firewall (the DNS filter then sees the firewall
  address for redirected queries; clients that use it through DHCP keep their own address).
  Devices in a group with a profile may not use their own DNS server at all (default), so the profile cannot
  be bypassed.

### Filter backends

| | AdGuard Home | Pi-hole v6 |
|---|---|---|
| Own group/client per plugin group | yes | yes |
| SafeSearch per group | yes | no (not available per client) |
| Adult content filter | yes | through your own blocklist in a Pi-hole group |
| Categories (social, games, ...) | yes (services) | no |
| Extra blocked/allowed domains | yes | yes |
| Point to existing groups in the backend | no | yes |
| Query log statistics | yes | not yet |

The plugin only manages its own items (clients and groups named `PC: <group>`, and a marked block of user
rules). Everything you configured yourself stays untouched. The Pi-hole integration is experimental: it is
tested against a simulated API, not yet against a real Pi-hole. To add a backend, write a module with an API
class (`__enter__`/`__exit__`, `describe()`, `test()`) and a `sync(api, desired)` function (see `pc_pihole.py`),
register it in `pc_backends.py` and add it as an option to the model.

## Limitations

* Devices with a random (private) MAC address must use a fixed address for your network.
* A new IPv6 privacy address is only blocked at the next check (10 seconds by default).
* HTTPS sites cannot be redirected without certificate errors: the browser shows a connection error. The block
  page appears for http:// addresses and through the automatic captive portal check.
* The firewall only sees network traffic, not screen time; the threshold and grace period are approximations.
* Gambling has no category (AdGuard has no service for it): use "extra blocked domains" or a blocklist.
* The backend password is stored in the OPNsense configuration; use a dedicated user or app password.
  AdGuard's adult filter sends hashed domain prefixes to the AdGuard service.
* Most used apps, most visited sites and recent queries depend on the query log of the backend (AdGuard Home
  keeps it for a limited time; the page reads at most the most recent 5000 queries per request).

## Install (testing)

On an OPNsense test machine, as root:

```sh
fetch https://github.com/<user>/os-parentalcontrol/archive/refs/heads/main.tar.gz
tar xzf main.tar.gz && cd os-parentalcontrol-main
./install-dev.sh
```

This copies `src/` to `/usr/local/`. It is a development method: an OPNsense upgrade may remove the files.

## Building a package

The `Makefile` is meant for the OPNsense plugins tree: clone <https://github.com/opnsense/plugins>, copy this
directory to `security/parentalcontrol` and build with the OPNsense tooling (`make package`). Publish the
resulting `.pkg` as a release and install it with `pkg add`.

## Development

```sh
python3 -m unittest discover -s tests -v          # Python standard library only
cd tests/js && npm install && npm test            # browser test of the GUI pages (jsdom)
```

The tests simulate `pfctl`, `arp`, `ndp`, `ifconfig`, AdGuard Home and Pi-hole (see `tests/helpers.py`). They do
not prove that pf accepts the rules on a real OPNsense: always test on a VM. GitHub Actions
(`.github/workflows/ci.yml`) also runs `php -l`, `xmllint` and a shell syntax check.

## Translations

* Source strings are English. In the GUI they use the standard OPNsense mechanisms (`lang._()` in Volt,
  `gettext()` in PHP, labels and help in the form XML), so OPNsense's own translation process (gettext/POEditor)
  can pick them up when the plugin is part of the official plugins repository.
* Text rendered outside the GUI (the block page, notifications, backend messages) is translated by
  `pc_i18n.py` from `src/opnsense/scripts/OPNsense/ParentalControl/lang/<locale>.po`; the locale is the OPNsense
  system language. Dutch (`nl_NL.po`) is included and complete.
* `tools/make_pot.py` extracts all strings into `lang/parentalcontrol.pot`; `tools/make_pot.py --merge` adds new
  strings to every `.po` file. A test fails if the template is out of date or a translation is incomplete.
* To add a language: copy `parentalcontrol.pot` to `<locale>.po` (for example `de_DE.po`), translate it, keep the
  `%s`/`%d` placeholders, and run the tests.

## Directory structure

```
Makefile, pkg-descr          package definition
src/etc/inc/plugins.inc.d/   plugin hooks (service, boot, firewall anchors, syslog)
src/opnsense/mvc/app/        model, controllers, forms, views (Overview, Administration), menu, ACL
src/opnsense/scripts/        daemon, backends, statistics, helper scripts, translations (lang/)
src/opnsense/service/        configd actions, syslog-ng template for the log file
tests/, tools/               unit tests, browser test, string extraction
```

## License

BSD 2-Clause, see [LICENSE](LICENSE).
