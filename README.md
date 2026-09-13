# Urlaub-Tracker — Flug- & Hotel-Preistracker Koh Samui 2027

Findet und trackt automatisiert die günstigste Kombination aus **Flug** und
**Hotel** (Santiburi Koh Samui, 8 Personen, 3 Villen, 14 Nächte, Mai 2027),
speichert den Verlauf und zeigt in einem Web-Dashboard, was gerade günstig
oder teuer ist.

---

## Ehrliche Einordnung der Datenquellen (Stand September 2026)

Der ursprüngliche Plan sah Kiwi Tequila + Amadeus Self-Service vor. Beide sind
**nicht mehr nutzbar**:

| Quelle | Status | Konsequenz |
|---|---|---|
| **Kiwi.com Tequila API** | seit Mai 2024 nur noch *auf Einladung* für qualifizierte Reiseunternehmen | als Privatperson kein Zugang |
| **Amadeus Self-Service API** | Self-Service-Angebot **am 17.07.2026 abgeschaltet** (GitHub-Orga archiviert, nur noch Enterprise) | kein Free Tier mehr |
| **SerpApi / Google Flights API** | funktioniert, aber kostenpflichtig | vom Nutzer bewusst **nicht** gewählt |

**Tatsächlich genutzt:**

* **Flüge:** [`fast-flights`](https://github.com/AWeirdDev/flights) — inoffizielle
  Google-Flights-Bibliothek. Liefert Airline, **Flugzeugtyp pro Segment**,
  Segmentzeiten, Umsteigeflughäfen, Preis, CO₂. Inoffiziell → jede Suche ist
  gekapselt, leere Ergebnisse sind normal.
  * **Nur zusammenhängende Buchungen:** gesucht (und angezeigt) werden
    ausschließlich **Round-Trip** (gleicher Flughafen, ein Ticket — bei Koh
    Samui **~halb so teuer** wie zwei Einzelrichtungs-Tickets) und
    **Multi-City** (Hinflug-Airport ≠ Rückflug-Airport, z. B. hin ab Zürich /
    zurück nach München, trotzdem ein Ticket mit geschützten Anschlüssen).
    Reine Einzelrichtungs-Suchen gibt es nur noch optional für Debug-Zwecke
    (`sources.flights.oneway_legs`, Default aus) und tauchen nirgends als
    Buchungsoption auf.
  * **EU-Consent:** Google leitet ohne Cookie auf `consent.google.com` um →
    HTML wird selbst geholt (primp/Chrome-Impersonation + Cookie `SOCS=CAI`).
  * **Blockade-Erkennung:** „unusual traffic" → eigener Fehler + eine
    Wiederholung, danach als `blocked_queries` im Health sichtbar (nicht als
    „keine Flüge" verschleiert). Default: Check alle 6 h, 10–22 s Abstand.
  * **Gruppensuche:** primär Suche mit **1 Passagier**, Preis ×8
    (`price_is_total_for_all_pax: false`) — schneller/robuster als eine echte
    8-Pax-Suche. **Aber:** verifiziert (11.09.26), dass Google für 8 Passagiere
    tatsächlich *andere* (oft teurere) Ergebnisse liefert als die 1-Pax-Suche
    erwarten lässt — z. B. war die günstigste 1-Pax-Kombination (Qatar Airways,
    908 €/p.P.) für 8 Personen zusammen gar nicht verfügbar, real nur Condor ab
    1.150 €/p.P. Deshalb **`sources.flights.group_check`**: die
    `group_check_top_n` günstigsten Kombinationen werden zusätzlich ECHT mit
    `Passengers(adults=8)` nachgeprüft; bestätigt die Gruppensuche den 1-Pax-Preis
    nicht, wird die Kombination ausgeschlossen (Badge „Gruppe geprüft" im
    Dashboard markiert bestätigte Zeilen).
  * **Rückflug 28. oder 29.05. — beides wird echt verglichen, inkl. der
    passenden Hotel-Nächte.** `trip.date_pairs` sucht beide Rückflugtermine
    als eigene Kombination (fester Abflug 14.05.). **Bugfix (12.09.26):**
    vorher wählte `logic/combine.py` schlicht den *nackten* günstigsten
    Flugpreis — ein Rückflug am 29. braucht aber eine Hotelnacht mehr als
    der fest hinterlegte 13-Nächte-Hotelpreis einpreist, ein auf dem Papier
    günstigerer Flug hätte so in Wahrheit teurer sein können, ohne dass das
    auffällt. Jetzt wird zuerst das Hotel gewählt, dann der Flug nach dem
    **echten Gesamtpreis** (Flug + zum tatsächlichen Rückflugdatum
    passender, per Preis/Nacht hoch-/runtergerechneter Hotelanteil) —
    siehe `_true_total`/`_hotel_nights_delta` in `logic/combine.py`.
  * **Bekannte Lücke: Stuttgart & München liefern aktuell keine Daten.**
    Der `fast-flights`-Parser wirft für STR/MUC→USM reproduzierbar einen
    `TypeError` (`payload[3] is None`), obwohl echte Flüge existieren (manuell
    in Google Flights nachgeprüft: STR→USM 14.–28.05.2027 ab 1.043 €/p.P.,
    KLM/Bangkok Airways). Vermutlich berechnet Google für diese aufwändigeren
    Routings die Ergebnisse asynchron nach, unsere synchrone SSR-Abfrage sieht
    nur den leeren Zwischenstand. Das wird **nicht** mehr stillschweigend als
    „keine Flüge" verschleiert (das war der ursprüngliche Bug), sondern als
    `failed_queries` im Health sichtbar gemacht, inkl. fertigem Deep-Link zum
    manuellen Nachschauen (Dashboard-Abschnitt „Quellen-Status"). Eine echte
    Behebung würde eine andere Scraping-Strategie brauchen (z. B. auf das
    asynchrone Nachladen warten) — als Erweiterung offen.
  * Buchungslink = **Google-Flights-Such-Deeplink**, kein Airline-Itinerary-Link.
* **ITA Matrix** (`app/sources/flights_ita_matrix.py`, matrix.itasoftware.com,
  Googles eigenes Fare-Research-Tool) — findet manchmal Routings/Tarife, die
  die normale Google-Flights-Suche nicht zeigt, ist aber **nicht buchbar**.
  Deshalb eine eigene Dashboard-Kategorie „Recherche-Referenz", **nie** mit
  den buchbaren Angeboten vermischt und **nicht** Teil des Vergleichs/Verdicts.
  Deep-Link-Mechanik wie bei CHECK24 & Co.: base64-kodiertes JSON im
  URL-Parameter `search` (`matrix.itasoftware.com/flights?search=<b64>`),
  kein Formular-Ausfüllen nötig. ITA Matrix rechnet spürbar langsamer als
  Google Flights (häufig 25–40 s je Suche) — 1-Pax-Suche × Personenzahl
  hochgerechnet, dazu ein Deep-Link mit der echten Personenzahl (lädt lang,
  aber der Nutzer kann selbst warten) und ein Cross-Check-Link zur normalen
  (buchbaren) Google-Flights-Suche für dieselbe Route/Termine.
  * **Bekannte Einschränkung (manuell live verifiziert 11.09.26):** Für unsere
    konkrete Route (STR/MUC/FRA/ZRH → USM, Mai 2027) liefert ITA Matrix
    innerhalb von 90 s **keinen** Preis — auch ein manueller Test im Browser
    (über 2 Minuten gewartet, kein Timeout im Code) blieb ohne Ergebnis, die
    Seite zeigt nur einen endlosen Lade-Spinner statt einer "keine Tarife
    gefunden"-Meldung. Das ist **kein Bug in unserem Scraper** (Deep-Link,
    JSON-Payload und Preis-Extraktion sind korrekt verdrahtet und funktionieren
    grundsätzlich, siehe Docstring in `flights_ita_matrix.py`), sondern eine
    echte Grenze von ITA Matrix bei dieser eher exotischen Langstrecke zu einer
    kleinen Insel (USM) — vermutlich kann das Fare-Construction-System keine
    gültige Tarifkombination für diese Route berechnen. Die Deep-Links bleiben
    im Dashboard trotzdem sichtbar (der Nutzer kann selbst beliebig lange
    warten), die Preis-Spalte zeigt in diesem Fall aber dauerhaft "–".
* **Hotel:** fünf Quellen, in dieser Prioritätsreihenfolge (konkreter Preis
  schlägt manuelle Referenz schlägt Floor-Richtwert):
  1. **CHECK24**, **Santiburi direkt** und **lastminute.com** (je eine Datei
     in `app/sources/hotels/`) — alle **gleichberechtigt primär**, alle der
     **genaue** Live-Preis. Alle drei rechnen "3 Zimmer/8 Personen" auf einmal
     oft schlecht; zuverlässig ist stattdessen, **jede Zimmergröße einzeln zu
     suchen** (`1 Zimmer, 2 Erwachsene` / `1 Zimmer, 3 Erwachsene`) und die
     günstigsten Treffer je Größe × Anzahl Zimmer dieser Größe zu summieren —
     bei 8 Pers./3 Zimmern (max. 3/Zimmer) automatisch `2× (1 Zi./3 Erw.) +
     1× (1 Zi./2 Erw.)`. Santiburis eigene Buchungsmaschine (SynXis-IBE unter
     `reservation.santiburisamui.com`, verlinkt von santiburisamui.com über
     "BOOK NOW") liefert dabei sogar ganz ohne OTA-Marge. Alle drei technisch
     ein Deep-Link mit Termin/Belegung direkt in der URL statt
     Formulareingabe (z. B. CHECK24:
     `hotel.check24.de/search/<Hotel>-<ID>/<checkin>/<checkout>/[A|A|A]/hotel.html`) —
     **das Dashboard verlinkt jede Zimmergröße einzeln**, nicht nur einen
     Gesamtlink. Playwright, **bewusst langsam** (20–45 s Pause zwischen den
     Teil-Suchen — beim 4–6-h-Takt unkritisch, schont die Seiten und senkt
     das Bot-Erkennungsrisiko). Erfassen auch den kostenlos stornierbaren
     Preis separat (`raw.refundable_total`).
  2. **`reference_offers` in `config.yaml`** — selbst recherchierte Preise
     (Fallback/Sanity-Check, falls alle obigen mal blockiert sind) laufen
     ebenfalls als vollwertige Angebote in Vergleich, Trend und Alarm.
  3. **Google Hotels** (`app/sources/hotels/google_hotels.py`) — *eine*
     Server-HTML-Anfrage, gleiche `SOCS=CAI`-Masche wie bei den Flügen (kein
     Browser). Aggregiert ~20 OTAs + Zimmertypen, aber Google ignoriert die
     Belegung in der URL → nur ein **Floor-Richtwert** (günstigster sichtbarer
     Nachtpreis × 3 × 13, ohne 3-Personen-Aufpreis), klar so markiert.
  **Expedia** (`app/sources/hotels/expedia.py`) ist gebaut (gleicher
  Deep-Link-Trick, URL bekannt), aber **per Default aus**: Playwright bekommt
  dort zuverlässig einen Slider-Captcha ("Zeige uns deine menschliche
  Seite") — Bot-Erkennung wird **nicht** automatisiert umgangen (Policy).
  Der Deep-Link im Dashboard funktioniert trotzdem zum manuellen Aufrufen;
  dort lässt sich auch ein Corporate-Rabattcode anwenden (wird von uns
  absichtlich **nicht** automatisiert/eingerechnet — der öffentliche Preis
  wird angezeigt, den Rabatt darauf rechnet man manuell). Booking als
  weiterer Einzelscraper ist ebenfalls noch da, per Default aus.
  * **Hotel-Zeitraum ≠ Flug-Zeitraum:** `trip.hotel_checkin`/`hotel_checkout`
    sind eigene Config-Felder. Hinflug startet z. B. am 14.05. in Deutschland,
    durch Nachtflug/Zeitverschiebung ist man aber erst ab dem 15.05. in
    Thailand — das Hotel wird entsprechend erst ab dann gebucht (13 statt
    14 Nächte).
* **Pauschalreisen:** primär **CHECK24** (`app/sources/packages/check24.py`)
  — gleicher Zimmergrößen-Split wie beim Hotelvergleich, nur dass jedes
  Ergebnis Flug **und** Hotel als ein Paket enthält
  (`urlaub.check24.de/suche/hotel?...&roomAllocation=A-A-A&pageArea=package`).
  ZRH ist dort nicht buchbar (nur deutsche Abflughäfen: STR/MUC/FRA).
  Zusätzlich TUI, DERTOUR, alltours, REWE Reisen, sonnenklar.TV — generischer
  Playwright-Scraper, ein `PortalSpec` (URL-Vorlage + Selektor-Kandidaten) pro
  Portal in [`app/sources/packages/specs.py`](app/sources/packages/specs.py).

> **Wichtig:** Die Scraper-Selektoren sind Startwerte und müssen gegen die
> Live-Seiten nachgezogen werden. Für Mai 2027 liefern die meisten
> Hotel-/Pauschalportale **jetzt noch gar nichts** — das ist erwartetes
> Verhalten, kein Fehler. Das Tool wird mit der Zeit vollständiger.

---

## Was das Dashboard zeigt

* **Trend-Ampel** pro Kategorie (Flug / Hotel / Pauschal): aktueller Preis vs.
  Mittel der letzten *N* Checks → „gerade günstig / im Durchschnitt / gerade teuer“.
* **Einzelbuchung (Flug + Hotel getrennt) vs. günstigste Pauschalreise**, klar
  gegenübergestellt, Gewinner hervorgehoben.
* **Günstigster Flug aktuell** (unabhängig vom Hotel) mit allen Details:
  Airline(s) pro Segment, Flugzeugtyp, Abflug-/Ankunftszeiten inkl. Zeitzone,
  Stopps + Layover-Flughäfen + -Dauer, Gesamtreisezeit, Preis p. P. und ×8,
  Deep-Link, Erfassungs-Zeitstempel, CO₂. Bei Round-Trip zusätzlich eine
  **Rückflug-Referenzkarte** — Google zeigt beim Round-Trip selbst nur den
  Hinflug im Detail; eine separate Einzelrichtungs-Suche (selber Flughafen/
  Termin) zeigt ein plausibles Rückflug-Routing dazu. Rein informativ, nicht
  Teil des Ticketpreises.
* **Historischer Preisverlauf** als Linienchart (Chart.js, lokal gebündelt).
* **Filter-/sortierbare Tabelle** aller zusammenhängenden Buchungen (nur
  Round-Trip/Multi-City) — sortierbar nach Preis gesamt, Preis p. P., Stopps,
  Reisezeit, Abflugzeit, Abflughafen, Airline; auf- oder absteigend, per
  Dropdown oder Klick auf die Spaltenüberschrift. Filter nach Ticket-Typ,
  Abflughafen, Airline, max. Stopps. Ausgefilterte Optionen bleiben sichtbar
  (ausgegraut, mit Begründung).
* **Quellen-Status** des letzten Checks.

### Harte Flug-Constraints (in `config.yaml`)

0. **Nur zusammenhängende Buchungen (ein Ticket).** Es werden ausschließlich
   **Round-Trip** (gleicher Flughafen Hin/Rück) und **Multi-City** (Hinflug-
   Airport ≠ Rückflug-Airport, z. B. hin ab Zürich, zurück nach München —
   trotzdem EIN Ticket) gesucht und angezeigt. Reine Einzelrichtungs-Tickets
   fließen nirgends in Vergleich oder Dashboard ein — bei Verspätung ist so
   immer die Airline für die Umbuchung des Anschlusses zuständig. Der letzte
   Leg BKK→USM (Bangkok Airways) ist auf den passenden Routen automatisch Teil
   *eines* Tickets (Interline/Codeshare), es gibt keinen separaten Zubringer.
   Zusätzlich: **`single_ticket_only: true`** blendet auch innerhalb einer
   Buchung getrennte Tickets/Self-Transfer aus.
1. **Abreisetag-Zeitfenster:** erster Leg am Hinflug-Starttag nicht vor
   `outbound_earliest_departure` (Default 15:00 — Anfahrt Schwäbisch Gmünd,
   Ankunft Flughafen ~14:30 + Check-in-Puffer). Gilt nur Hinflug, nur erster Leg.
2. **Max. 2 Stopps pro Richtung** — bei Multi-City Hin- und Rückstrecke
   getrennt geprüft (nicht die Summe).
3. **Unsinnige Umwege** raus: Gesamt-Routendistanz > `max_detour_ratio` ×
   Direktdistanz, oder ein Zwischenstopp liegt > 20 % weiter vom Ziel weg als
   der Start (Rückwärts-Routing / falscher Kontinent).
4. **Max. Reisezeit** ebenfalls pro Richtung geprüft (die Zeit *vor Ort*
   zwischen Hin- und Rückflug zählt nicht mit).
5. Layover-Sanity (`min_layover_minutes`, `max_layover_hours`) und
   `max_total_travel_hours`.
6. **Airline-Ausschluss:** `excluded_airlines` (Default `["Austrian"]`,
   Teilstring-Match) schließt Airlines immer aus. `exclude_gulf_carriers`
   (Default `false`) ist ein Schalter für Qatar Airways, Etihad, Emirates,
   Saudia, Gulf Air, Oman Air, Royal Jordanian — einfach auf `true` stellen,
   wenn die Entscheidung gegen die Golfstaaten-Carrier fällt.

---

## Schnellstart (lokal, Windows/macOS/Linux)

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate       macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium

cp config.example.yaml config.yaml        # Reiseparameter anpassen
cp .env.example .env                      # optional: SMTP / ntfy / Proxy

# Ein einzelner Check (schreibt in die DB):
python -m app.check

# Dashboard + interner Scheduler:
uvicorn app.main:app --reload
# -> http://127.0.0.1:8000
```

SQLite-DB landet unter `data/prices.db`. Für PostgreSQL (z. B. auf dem
Hetzner-Server) in `.env`:

```
DATABASE_URL=postgresql+psycopg://user:pw@localhost:5432/urlaub_tracker
```

---

## Docker / Hetzner (Ubuntu)

Vollständiger Runbook: **[`deploy/README.md`](deploy/README.md)** (Docker **oder**
systemd, Reverse-Proxy mit Basic-Auth via [`deploy/Caddyfile`](deploy/Caddyfile),
Backup, Updates via [`deploy/deploy.sh`](deploy/deploy.sh)).

Kurz:

```bash
cp config.example.yaml config.yaml && cp .env.example .env   # anpassen
docker compose up -d --build
# Dashboard: http://127.0.0.1:8000  (nur lokal gebunden - Proxy davor!)
```

`config.yaml`, `data/`, `logs/`, `artifacts/` werden als Volumes gemountet und
bleiben erhalten. Der Container bringt Chromium für Playwright mit.

> Das Dashboard hat **keine eigene Anmeldung**. Auf einem öffentlichen Server
> davor zwingend Caddy/nginx mit Basic-Auth **oder** nur über VPN/Tailscale
> erreichbar machen (Port 8000 nicht freigeben).

**Scheduler-Optionen:**

* **Intern (Default):** `schedule.enabled: true` in `config.yaml`
  (APScheduler, alle `every_hours` Stunden, mit Jitter).
* **System-Cron:** `schedule.enabled: false` setzen, dann im Host-Crontab:

  ```
  0 */6 * * * docker exec urlaub-tracker python -m app.check --quiet
  ```

  bzw. ohne Docker:

  ```
  0 */6 * * * cd /opt/urlaub-tracker && /opt/urlaub-tracker/.venv/bin/python -m app.check --quiet
  ```

---

## Benachrichtigungen

Alarm, wenn ein Kategoriepreis (Flug / Hotel / `separate_total` / Pauschal)
eine Schwelle aus `alerts.thresholds` **unterschreitet** — max. einmal pro
`cooldown_hours` je Kategorie, und nur bei neuer Bestmarke.

* **E-Mail:** SMTP-Zugang in `.env` (`SMTP_HOST`, `SMTP_USER`, …, `ALERT_EMAIL_TO`).
* **Push:** [ntfy](https://ntfy.sh) — `NTFY_URL` + `NTFY_TOPIC` (selbst-hostbar,
  kein Account nötig). Optional `NTFY_TOKEN` für geschützte Server.

Fehlt die Config eines Kanals, wird er still übersprungen.

---

## API (Auszug)

| Endpoint | Zweck |
|---|---|
| `GET /api/summary` | Ampel, Vergleich, günstigster Flug |
| `GET /api/flights?direction=round_trip\|multi_city&origin=&airline=&max_stops=&sort=price\|price_per_person\|stops\|duration\|departure\|origin\|airline&descending=&include_excluded=` | Flugtabelle (nur zusammenhängende Buchungen) |
| `GET /api/hotels` · `GET /api/packages` | Hotel-/Pauschaltabelle |
| `GET /api/history` | Zeitreihen für den Chart |
| `GET /api/status` · `GET /api/runs` | letzter Lauf, Scheduler, Historie |
| `GET /api/config` | aktive Konfiguration (ohne Secrets) |
| `POST /api/config/reload` | `config.yaml` neu einlesen |
| `POST /api/check/run` | Sofort-Check (Header `X-Trigger-Token`, falls gesetzt) |

---

## Projektstruktur

```
app/
  config.py            YAML + .env -> validierte Config
  collector.py         Orchestrator: ein kompletter Check
  scheduler.py         APScheduler (alle N h)
  notify.py            E-Mail (SMTP) + ntfy
  api.py / main.py     FastAPI + statisches Dashboard
  check.py             CLI:  python -m app.check
  geo.py               Flughafen-Geodaten, Distanzen (airportsdata)
  offers.py            normalisierte Angebots-Dataclasses
  models.py            SQLAlchemy: CheckRun, *OfferRow, BestSnapshot, AlertLog
  logic/
    route_filter.py    harte Flug-Constraints
    normalize.py       Dedup (günstigster Preis/Link gewinnt)
    combine.py         Einzelbuchung vs. Pauschalreise
    trends.py          Ampel + Zeitreihen
  sources/
    flights.py                 fast-flights + Consent-Fix + 1-Pax-Hochrechnung + Gruppen-Check
    flights_ita_matrix.py       ITA Matrix (nicht buchbar, eigene Recherche-Kategorie)
    scraper_base.py             gemeinsame Playwright-Helfer, Preis-Parsing
    hotels/  google_hotels (primaer, SSR) + booking/expedia/santiburi (Fallback, aus)
    packages/  portal_base + specs (TUI, DERTOUR, alltours, REWE, sonnenklar)
frontend/  index.html, app.js, style.css, vendor/chart.umd.min.js
tests/     route_filter, combine, trends, money-parsing
```

## Tests

```bash
pytest
```

## Rate-Limits im Blick behalten

`fast-flights` hat kein formales Limit, aber Google blockt bei zu vielen
Requests. Zwischen den Flugsuchen pro Check liegt eine zufällige Pause
(`sources.flights.request_delay_seconds`, Default 10–22 s). Bei Bedarf einen
Proxy in `.env` setzen (`FLIGHTS_PROXY`). Pro Check: ~32 Haupt-Abfragen (volle
Multi-City-Matrix + Round-Trip, beide Datumspaare) + bis zu `group_check_top_n`
(Default 6) zusätzliche Gruppen-Check-Abfragen mit der echten Personenzahl.

ITA Matrix (`sources.ita_matrix`) läuft komplett getrennt, ein Playwright-Aufruf
je Heimatflughafen (Default 4), 15–30 s Pause dazwischen
(`sources.ita_matrix.request_delay_seconds`) — jede Einzelsuche dauert bei
ITA Matrix selbst schon 25–40 s, da bewusst kein Zeitdruck.

CHECK24 bekommt pro Check nur **2 Playwright-Aufrufe** (eine Suche je
Zimmergröße), mit 20–45 s Pause dazwischen (`sources.hotels.check24_delay_seconds`)
— absichtlich gemütlich getaktet, weil der Check ohnehin nur alle paar
Stunden läuft und Zeitdruck hier nichts bringt, nur unnötiges Blockrisiko.
