#!/usr/bin/env bash
# Auf dem Server ausfuehren: holt den aktuellen Stand und startet neu.
# Variante Docker (Standard) oder --systemd.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/urlaub-tracker}"
MODE="${1:-docker}"

cd "$APP_DIR"

echo ">> git pull"
git pull --ff-only

if [[ "$MODE" == "--systemd" || "$MODE" == "systemd" ]]; then
    echo ">> venv aktualisieren"
    ./.venv/bin/pip install -q -r requirements.txt
    ./.venv/bin/python -m playwright install chromium
    echo ">> Dienst neu starten"
    sudo systemctl restart urlaub-tracker
    sudo systemctl --no-pager status urlaub-tracker | head -n 12
else
    echo ">> Docker build + up"
    docker compose up -d --build
    docker compose ps
    echo ">> Logs (Ctrl-C zum Beenden):"
    docker compose logs -f --tail=40
fi
