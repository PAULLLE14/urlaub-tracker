"""REST-API fuers Dashboard."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Query

from . import live_progress, queries, scheduler
from .config import get_config, get_secrets, reload
from .database import get_session
from .logging_setup import get_logger

log = get_logger("api")
router = APIRouter(prefix="/api")


def _session():
    s = get_session()
    try:
        yield s
    finally:
        s.close()


@router.get("/status")
def status():
    s = get_session()
    try:
        return queries.status_payload(s, get_config(), scheduler.info())
    finally:
        s.close()


@router.get("/live_progress")
def live_progress_status():
    # Nutzer-Fund 16.09.26: rein ephemerer Fortschritts-Kanal fuer den
    # AKTUELL laufenden Check (siehe live_progress.py Docstring) - keine
    # DB-Abfrage, kein Ersatz fuer /api/summary nach Abschluss des Laufs.
    return live_progress.snapshot()


@router.get("/summary")
def summary():
    s = get_session()
    try:
        return queries.summary_payload(s, get_config())
    finally:
        s.close()


@router.get("/flights")
def flights(
    run: str | None = Query(None),
    direction: str | None = Query(None, pattern="^(round_trip|multi_city)$"),
    origin: str | None = None,
    airline: str | None = None,
    max_stops: int | None = None,
    include_excluded: bool = True,
    sort: str = Query("price", pattern="^(price|price_per_person|stops|duration|departure|origin|airline)$"),
    descending: bool = False,
):
    s = get_session()
    try:
        return queries.flights_table(
            s, get_config(), run=run, direction=direction, origin=origin,
            airline=airline, max_stops=max_stops,
            include_excluded=include_excluded, sort=sort, descending=descending,
        )
    finally:
        s.close()


@router.get("/ita_matrix")
def ita_matrix(run: str | None = Query(None)):
    s = get_session()
    try:
        return queries.ita_matrix_table(s, run)
    finally:
        s.close()


@router.get("/hotels")
def hotels(run: str | None = Query(None)):
    s = get_session()
    try:
        return queries.hotels_table(s, run)
    finally:
        s.close()


@router.get("/packages")
def packages(run: str | None = Query(None)):
    s = get_session()
    try:
        return queries.packages_table(s, run)
    finally:
        s.close()


@router.get("/history")
def history():
    s = get_session()
    try:
        return queries.history_payload(s, get_config())
    finally:
        s.close()


@router.get("/runs")
def runs(limit: int = 30):
    s = get_session()
    try:
        return {"runs": queries.runs_list(s, limit)}
    finally:
        s.close()


@router.get("/config")
def config_echo():
    cfg = get_config()
    data = cfg.model_dump(mode="json")
    return data  # enthaelt keine Secrets


@router.post("/config/reload")
def config_reload():
    reload()
    return {"reloaded": True, "trip": get_config().trip.label}


@router.post("/check/run")
def check_run(background: BackgroundTasks,
              x_trigger_token: str | None = Header(None)):
    sec = get_secrets()
    if sec.trigger_token and x_trigger_token != sec.trigger_token:
        raise HTTPException(status_code=401, detail="ungueltiges Trigger-Token")
    scheduler.trigger_async()
    return {"started": True, "note": "Check laeuft im Hintergrund, /api/status zeigt Fortschritt"}
