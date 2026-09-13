"""FastAPI-App: API + statisches Dashboard + Scheduler-Lifecycle.

Start lokal:   uvicorn app.main:app --reload
Start Server:  uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import scheduler
from .api import router as api_router
from .config import get_config
from .database import init_db
from .logging_setup import get_logger, setup_logging

setup_logging()
log = get_logger("main")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    cfg = get_config()
    log.info("Urlaub-Tracker startet - Reise: %s", cfg.trip.label)
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown()


app = FastAPI(title="Urlaub-Tracker", version="0.1.0", lifespan=lifespan)
app.include_router(api_router)


@app.middleware("http")
async def no_cache_headers(request, call_next):
    """Erzwingt eine Revalidierung bei JEDEM Laden (Browser fragt den Server
    "hat sich was geaendert?" statt blind die alte lokale Kopie von
    index.html/app.js/style.css weiterzuverwenden). Ohne das hat ein Nutzer
    nach einem Deploy reproduzierbar eine veraltete Seite gesehen (12.09.26)
    - die Daten (API-Antworten) waren schon aktuell, nur HTML/CSS/JS kamen
    noch aus dem Browser-Cache. "no-cache" heisst NICHT "nie cachen", sondern
    "vor jeder Nutzung beim Server nachfragen" - bei unveraendertem Inhalt
    liefert das ein schnelles 304, kein voller Re-Download noetig."""
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
