# Deployment auf einem Hetzner-Server (Ubuntu 22.04/24.04)

Zwei Wege. **Docker** ist am einfachsten und identisch zu lokal. **systemd**
spart den Docker-Overhead. Beide setzen einen kleinen Reverse-Proxy davor,
weil das Dashboard selbst **keine Anmeldung** hat.

---

## Variante A — Docker (empfohlen)

```bash
# als root oder via sudo
# ACHTUNG: auf Ubuntu 24.04 heisst das Compose-Paket "docker-compose-v2",
# NICHT "docker-compose-plugin" (das gibt's in den Ubuntu-Repos nicht -
# verifiziert 11.09.26 beim echten Deploy, apt bricht sonst die ganze
# Installation inkl. docker.io ab).
apt update && apt install -y docker.io docker-compose-v2 git
adduser --system --group --home /opt/urlaub-tracker tracker

git clone <DEIN_REPO_ODER_RSYNC> /opt/urlaub-tracker
cd /opt/urlaub-tracker

cp config.example.yaml config.yaml   # Reisedaten/Schwellen anpassen
cp .env.example .env                 # SMTP / ntfy / TRIGGER_TOKEN eintragen

docker compose up -d --build
docker compose logs -f --tail=50     # erster Lauf
```

Container-Neustart bei Reboot: `restart: unless-stopped` ist in
`docker-compose.yml` gesetzt.

**Einmaliger Sofort-Check:**
`docker exec urlaub-tracker python -m app.check --quiet`

---

## Variante B — systemd + venv (ohne Docker)

```bash
apt update && apt install -y python3.11 python3.11-venv git \
  libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
  libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
  libgbm1 libasound2                       # Chromium-Abhaengigkeiten

adduser --system --group --home /opt/urlaub-tracker tracker
git clone <REPO> /opt/urlaub-tracker && cd /opt/urlaub-tracker
chown -R tracker:tracker .

sudo -u tracker python3.11 -m venv .venv
sudo -u tracker ./.venv/bin/pip install -r requirements.txt
sudo -u tracker ./.venv/bin/python -m playwright install chromium

cp config.example.yaml config.yaml && cp .env.example .env   # anpassen

cp deploy/urlaub-tracker.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now urlaub-tracker
journalctl -u urlaub-tracker -f
```

**Scheduler:** intern (APScheduler, `schedule.enabled: true`) — nichts weiter
nötig. Alternativ System-Cron: `schedule.enabled: false` und
`crontab -e -u tracker`:

```
0 */6 * * * cd /opt/urlaub-tracker && ./.venv/bin/python -m app.check --quiet
```

---

## Reverse-Proxy + Basic-Auth (beide Varianten)

Das Dashboard lauscht nur auf `127.0.0.1:8000`. Davor **Caddy** (macht HTTPS
automatisch):

```bash
apt install -y caddy
caddy hash-password --plaintext 'DEIN_DASHBOARD_PASSWORT'   # Hash notieren

cp /opt/urlaub-tracker/deploy/Caddyfile /etc/caddy/Caddyfile
# Domain + Hash in /etc/caddy/Caddyfile eintragen, A-Record auf Server-IP
systemctl restart caddy
```

Kein eigenes Domain? Dann Server nur über **Tailscale/WireGuard** erreichbar
machen und in der Firewall Port 8000 dichthalten:

```bash
ufw allow 22/tcp && ufw allow 443/tcp && ufw enable
# Port 8000 NICHT freigeben
```

### Variante C — nur die Server-IP, kein Domain, kein Tailscale

Wenn auf dem Server schon **nginx** läuft (statt Caddy, z.B. weil dort schon
andere Seiten via nginx+certbot bedient werden) und schlicht `http://<IP>:8080`
mit Basic-Auth reichen soll (kein TLS möglich ohne Domain - Let's Encrypt
braucht einen Hostnamen, das ist hier bewusst akzeptiert):

```bash
htpasswd -bc /etc/nginx/.htpasswd-urlaub urlaub 'DEIN_PASSWORT'
cp /opt/urlaub-tracker/deploy/nginx-urlaub-ip.conf /etc/nginx/sites-available/urlaub-tracker
ln -sf /etc/nginx/sites-available/urlaub-tracker /etc/nginx/sites-enabled/urlaub-tracker
nginx -t && systemctl reload nginx
```

Container-seitig bleibt es beim `127.0.0.1:8000:8000`-Binding aus
`docker-compose.yml` (nur nginx spricht mit dem Container, nicht das Internet
direkt) - so ist das Dashboard unter `http://<Server-IP>:8080/` mit
Basic-Auth-Login erreichbar, ohne Domain/DNS/Zertifikat.

---

## Updates einspielen

```bash
cd /opt/urlaub-tracker
./deploy/deploy.sh            # Docker
./deploy/deploy.sh --systemd  # venv/systemd
```

---

## Datensicherung

Alles Wichtige liegt in `data/` (SQLite `prices.db` mit der Preishistorie).
Täglich sichern reicht:

```
0 3 * * * cp /opt/urlaub-tracker/data/prices.db /opt/backups/prices-$(date +\%F).db
```

Bei PostgreSQL statt SQLite: `DATABASE_URL` in `.env` setzen und den
db-Service in `docker-compose.yml` einkommentieren (bzw. die vorhandene
Postgres-Instanz nutzen).

---

## Health / Betrieb

* `GET /healthz` → `{"ok": true}` (nutzt der Docker-Healthcheck)
* `GET /api/status` → letzter Lauf, nächster Scheduler-Termin, Quellen-Status
* `POST /api/check/run` mit Header `X-Trigger-Token: <TRIGGER_TOKEN aus .env>`
  → Sofort-Check
* Logs: `logs/tracker.log` (rotierend) bzw. `journalctl -u urlaub-tracker`
* Scraper-Fehlbilder: `artifacts/*.png`

## Rate-Limits

`fast-flights` (Google Flights) hat kein offizielles Limit, wird aber bei zu
vielen Requests kurzzeitig geblockt. Default: Check alle **6 h**, ~32 Abfragen
(Round-Trip + volle Multi-City-Matrix, beide Datumspaare) mit 10–22 s Abstand,
eine Wiederholung bei Blockade. Bei häufigen `blocked_queries` im Status:
Intervall erhöhen, `multicity_all_date_pairs: false` setzen oder
`FLIGHTS_PROXY` in `.env` setzen.

Hotels/Pauschalreisen (CHECK24, Santiburi, lastminute.com) bekommen pro Check
je 2 Playwright-Aufrufe (eine Suche je Zimmergröße), mit 20–45 s Pause
dazwischen (`sources.hotels.check24_delay_seconds` /
`sources.packages.check24_delay_seconds`) — bewusst gemütlich, weil der
Check ohnehin nur alle paar Stunden läuft.

Zusätzlich bis zu 6 Gruppen-Check-Abfragen (`sources.flights.group_check_top_n`)
mit der echten Personenzahl statt 1-Pax-Hochrechnung, sowie 4 ITA-Matrix-Abfragen
(`sources.ita_matrix`, je 25–40 s Eigenlaufzeit) — insgesamt läuft ein Check
inklusive aller Quellen typischerweise 15–25 Minuten, unkritisch beim 6-h-Takt.
Bekannte Lücke: Stuttgart/München liefern bei `fast-flights` aktuell keine
Flugdaten (Parser-Fehler, kein „echtes" Nichtverfügbar-sein) — taucht als
`failed_queries` im Status auf, siehe README.md.
