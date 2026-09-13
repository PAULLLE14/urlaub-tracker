# syntax=docker/dockerfile:1
# Bewusst auf "bookworm" gepinnt statt dem floatenden "python:3.11-slim" Tag:
# das zeigt inzwischen auf Debian 13 (trixie), dessen Paketnamen
# (fonts-unifont statt ttf-unifont etc.) nicht zu playwright==1.49.1s
# "install --with-deps"-Paketliste passen -> Build brach beim Deploy
# (11.09.26, Hetzner) mit "E: Package 'ttf-unifont' has no installation
# candidate" ab. Bookworm ist die Debian-Version, die diese Playwright-
# Version tatsaechlich unterstuetzt.
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# System-Abhaengigkeiten fuer Chromium (Playwright)
RUN apt-get update && apt-get install -y --no-install-recommends \
        wget gnupg ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt \
    && python -m playwright install --with-deps chromium

COPY app ./app
COPY frontend ./frontend
COPY config.example.yaml ./config.example.yaml

# Laufzeit-Verzeichnisse (werden i.d.R. als Volumes gemountet)
RUN mkdir -p data logs artifacts

EXPOSE 8000

# Healthcheck gegen den API-Endpoint
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
