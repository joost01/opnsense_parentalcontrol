# os-parentalcontrol

Ouderlijk toezicht voor OPNsense.

> **Status: vroege ontwikkelversie (stap 1).** Nog niet getest op alle OPNsense-versies.
> Gebruik eerst een testomgeving (VM).

## Functies

| Functie | Status |
|---|---|
| Groepen met een of meer apparaten (op MAC-adres) | stap 1 |
| Flexibele bedtijdschema's per dag/week | stap 1 |
| Apparaattijd per groep (gelijktijdig gebruik telt eenmaal) | gepland |
| Extra tijd en verlengingsverzoeken (per 15 min) met "tijd is op"-pagina | gepland |
| Websiteprofielen op leeftijd | gepland |
| SafeSearch afdwingen | gepland |

## Werking

- De plugin registreert een pf-anchor `parentalcontrol`.
- De daemon `parentalcontrold` (Python) bepaalt per groep of er een bedtijd actief is,
  koppelt MAC-adressen via ARP/NDP aan IP-adressen en houdt een pf-table per groep bij.
  Een vaste regel blokkeert verkeer van adressen in die table.
- Bestaande verbindingen worden bij het blokkeren verbroken (`pfctl -k`).

## Beperkingen

- Apparaten met een willekeurig (privacy-)MAC-adres moeten dat voor jouw netwerk uitzetten.
- Nieuwe IPv6-privacy-adressen worden pas bij de volgende controle (standaard 10 s) geblokkeerd.
- De firewall ziet alleen netwerkverkeer, geen schermtijd.

## Installeren (testen)

Op een OPNsense-testmachine, als root:

```sh
fetch https://github.com/<gebruiker>/os-parentalcontrol/archive/refs/heads/main.tar.gz
tar xzf main.tar.gz && cd os-parentalcontrol-main
./install-dev.sh
```

Dit kopieert `src/` naar `/usr/local/`. Het is een ontwikkelmethode: een
OPNsense-upgrade kan de bestanden verwijderen. Verwijderen: gebruik de
bestandslijst in `src/` en draai `configctl parentalcontrol stop`.

## Pakket bouwen

De `Makefile` is bedoeld voor de OPNsense plugins-tree:

1. Clone <https://github.com/opnsense/plugins>.
2. Kopieer deze map naar `security/parentalcontrol` in die tree.
3. Bouw met de OPNsense-tooling (zie de README van opnsense/plugins): `make package`.
4. Het resulterende `.pkg` kun je als release op GitHub publiceren en installeren met
   `pkg add <bestand>.pkg`.

## Mapstructuur

```
Makefile, pkg-descr          pakketdefinitie
src/etc/inc/plugins.inc.d/   plugin-hooks (service, boot, firewall-anchor)
src/opnsense/mvc/app/        model, controllers, forms, views
src/opnsense/scripts/        daemon en service-script
src/opnsense/service/        configd-acties
```

## Licentie

BSD 2-Clause, zie [LICENSE](LICENSE).
