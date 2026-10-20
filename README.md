# os-parentalcontrol

Ouderlijk toezicht voor OPNsense.

> **Status: vroege ontwikkelversie (stap 1 + verbruiksoverzicht).** Nog niet getest op alle OPNsense-versies.
> Gebruik eerst een testomgeving (VM).

## Functies

| Functie | Status |
|---|---|
| Groepen met een of meer apparaten (op MAC-adres), ook te kiezen uit DHCP-reserveringen/leases/ARP | klaar |
| Flexibele bedtijdschema's per dag/week, een schema voor meerdere groepen | klaar |
| Overzicht verbruikte tijd per groep (gelijktijdig gebruik telt eenmaal) | klaar (alleen meten) |
| Dagbudget per groep, blokkeren bij verstrijken tijd | gepland |
| Extra tijd en verlengingsverzoeken (per 15 min) met "tijd is op"-pagina | gepland |
| Websiteprofielen op leeftijd | gepland |
| SafeSearch afdwingen | gepland |

## Werking

- De plugin registreert een pf-anchor `parentalcontrol`.
- De daemon `parentalcontrold` (Python) bepaalt per groep of er een bedtijd actief is,
  koppelt MAC-adressen via ARP/NDP aan IP-adressen en houdt een pf-table per groep bij.
  Een vaste regel blokkeert verkeer van adressen in die table.
- Bestaande verbindingen worden bij het blokkeren verbroken (`pfctl -k`).
- Tijdtelling: elke tick telt de daemon per groep eenmaal de verstreken tijd mee als minstens
  een apparaat meer verkeer had dan de activiteitsdrempel (bytes-delta over de pf-states,
  `pfctl -ss -vv`). Korte pauzes tellen mee dankzij een instelbare naloop.
  Verbruik staat in `/var/db/parentalcontrol/usage.json` (35 dagen).

## Beperkingen

- Apparaten met een willekeurig (privacy-)MAC-adres moeten dat voor jouw netwerk uitzetten.
- Nieuwe IPv6-privacy-adressen worden pas bij de volgende controle (standaard 10 s) geblokkeerd.
- De firewall ziet alleen netwerkverkeer, geen schermtijd; de drempel en naloop zijn benaderingen.
- Bestaande bedtijden worden bij de update naar model 1.0.1 automatisch omgezet naar het nieuwe veld "groepen".

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
