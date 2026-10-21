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
| Dagbudget per groep (per weekdag instelbaar, een regel voor meerdere groepen), blokkeren als de tijd op is | klaar |
| Extra tijd handmatig toevoegen (+15/+30/+60 min in het overzicht) | klaar |
| Blokkeerpagina ("tijd op" / bedtijd) met verlengingsverzoeken (eenmaal per 15 min), goedkeuren/afwijzen in de GUI, melding via ntfy/webhook | klaar |
| DNS afdwingen: al het poort-53-verkeer (ook hard-gecodeerd 8.8.8.8 e.d.) naar AdGuard, DoT/bekende DoH-resolvers blokkeren | klaar |
| Websiteprofielen op leeftijd (via de AdGuard Home API) | gepland |
| SafeSearch afdwingen (via de AdGuard Home API) | gepland |

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

- Budgetten: de laagste passende regel per groep en dag geldt; de teller begint om middernacht
  opnieuw. Is budget plus extra tijd op, dan wordt de groep geblokkeerd zoals bij een bedtijd.
- Extra tijd gaat via een wachtrij (`/var/db/parentalcontrol/queue`) naar de daemon en werkt direct.

- Blokkeerpagina: voor geblokkeerde apparaten leidt pf (rdr in hetzelfde anchor) DNS (poort 53) om naar
  Unbound op localhost en HTTP (poort 80) naar een kleine webserver in de daemon die alleen op
  127.0.0.1/::1 luistert. Hierdoor verschijnt de pagina ook automatisch via de captive-portal-detectie
  van Android, iOS en Windows. Al het andere verkeer wordt geweigerd (`block return`).
  Kan pf de omleidingsregels niet laden, dan valt de daemon terug op alleen blokkeren (zie syslog).
- Verzoeken: een kind kan als de tijd op is 15/30/60 minuten vragen, maximaal een verzoek per
  instelbare periode (ook na afwijzen). De ouder krijgt een melding en beslist in de OPNsense-GUI
  (Overzicht); goedkeuren gebeurt dus altijd achter de beveiligde login.

- DNS afdwingen: op de gekozen interfaces (nooit WAN!) wordt al het DNS-verkeer van clients met pf (rdr)
  omgeleid naar de eerste opgegeven DNS-server (bijv. AdGuard Home); die server zelf is uitgezonderd.
  Staat de DNS-server in hetzelfde subnet als de clients, dan voegt de daemon automatisch een
  nat-regel toe zodat antwoorden via de firewall terugkomen. AdGuard ziet omgeleide queries dan met het
  adres van de firewall; queries van clients die gewoon AdGuard gebruiken (via DHCP) behouden hun eigen IP.
  Controle: `pfctl -sn | grep parentalcontrol` moet `rdr-anchor` (en `nat-anchor`) tonen.

## Beperkingen

- HTTPS-sites kunnen niet netjes worden omgeleid: de browser toont een verbindingsfout.
  Alleen http://-adressen en de automatische captive-portal-melding tonen de blokkeerpagina.
- Standaard wordt DNS van geblokkeerde apparaten omgeleid naar Unbound op 127.0.0.1. Gebruik je een
  externe DNS-server (bijv. AdGuard), vul dan 'DNS-server(s) voor geblokkeerde apparaten' in: DNS mag
  dan alleen naar die server en er wordt niets omgeleid.

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

## Ontwikkelen en testen

```sh
python3 -m unittest discover -s tests -v     # alleen Python-standaardbibliotheek nodig
```

De tests bootsen `pfctl`, `arp`, `ndp` en `ifconfig` na (zie `tests/helpers.py`) en dekken onder meer
bedtijden, tijdtelling, budgetten, blokkeren, verzoeken, de blokkeerpagina, meldingen en DNS-regels.
De GUI heeft een browser-test (`cd tests/js && npm install && npm test`, jsdom). GitHub Actions (`.github/workflows/ci.yml`) draait daarnaast `php -l`, `xmllint` en een shell-syntaxcheck.
Tests bewijzen niet dat pf de regels op een echte OPNsense accepteert: test dat altijd op een VM.

## Robuustheid

- Systeemcommando's hebben een timeout; een hangende `pfctl` laat de daemon niet vastlopen.
- De daemon draait onder `daemon(8) -r`: na een crash volgt automatisch een herstart.
- Faalt pf bij het laden, dan valt de daemon stapsgewijs terug (zonder DNS-afdwinging, daarna zonder
  blokkeerpagina) en logt waarom. De status blijft zichtbaar in de GUI.
- De blokkeerpagina luistert alleen lokaal en accepteert maximaal 32 gelijktijdige verbindingen.

## Mapstructuur

```
Makefile, pkg-descr          pakketdefinitie
tests/                       unittests (stdlib) en testhulpen
src/etc/inc/plugins.inc.d/   plugin-hooks (service, boot, firewall-anchor)
src/opnsense/mvc/app/        model, controllers, forms, views
src/opnsense/scripts/        daemon en service-script
src/opnsense/service/        configd-acties
```

## Licentie

BSD 2-Clause, zie [LICENSE](LICENSE).
