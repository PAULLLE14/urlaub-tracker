#!/bin/bash
# Startet einen virtuellen X-Server (Xvfb) im Hintergrund, bevor der
# eigentliche Befehl (uvicorn) laeuft - Playwright/Chromium bekommt dadurch
# ein echtes Display statt headless=True, siehe config.yaml
# sources.scraper.headless und Dockerfile-Kommentar.
#
# Bewusst NICHT "xvfb-run" (der Debian-Wrapper) benutzt: der erste Versuch
# (16.09.26) brach live mit "xauth command not found" ab, und selbst nach
# Nachruesten von xauth kam der Container nicht mehr hoch (kein Log-Output,
# kein Port-Listener - vermutlich haengt xvfb-run in seiner eigenen
# Bereitschafts-Erkennung). Dieses Skript macht die drei noetigen Schritte
# selbst, mit einer klaren, geloggten Bereitschaftspruefung statt einer
# Blackbox:
#   1. /tmp/.X11-unix anlegen (X11-Socket-Verzeichnis, existiert im
#      schlanken Debian-Image nicht automatisch).
#   2. Xvfb im Hintergrund starten, KEINE xauth-Authentifizierung (-auth
#      wird nicht gesetzt) - fuer eine rein containerinterne, nicht per TCP
#      erreichbare Anzeige (-nolisten tcp) reicht das.
#   3. Auf das Socket-File warten (max. 15s), dann erst den eigentlichen
#      Befehl starten - so ist im Log klar sichtbar, WENN/OB Xvfb je bereit
#      wurde, statt eines stillen Haengers.
set -e

mkdir -p /tmp/.X11-unix
chmod 1777 /tmp/.X11-unix

export DISPLAY=:99
Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp &
XVFB_PID=$!

echo "[entrypoint] Xvfb gestartet (PID $XVFB_PID), warte auf Bereitschaft..."
ready=0
for i in $(seq 1 30); do
    if [ -e /tmp/.X11-unix/X99 ]; then
        ready=1
        break
    fi
    sleep 0.5
done

if [ "$ready" = "1" ]; then
    echo "[entrypoint] Xvfb bereit (Versuch $i/30)"
else
    echo "[entrypoint] WARNUNG: Xvfb nach 15s nicht bereit - starte trotzdem (Playwright faellt dann ggf. auf einen Fehler zurueck, sichtbar in den Quell-Logs)"
fi

exec "$@"
