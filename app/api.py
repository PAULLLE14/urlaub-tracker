"""REST-API fuers Dashboard."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Body, Header, HTTPException, Query

from . import live_progress, queries, scheduler, sync
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
        return queries.status_payload(
            s, get_config(),
            sync.remote_scheduler_info() if sync.mode() == "receiver" else scheduler.info())
    finally:
        s.close()


@router.get("/live_progress")
def live_progress_status():
    # Nutzer-Fund 16.09.26: rein ephemerer Fortschritts-Kanal fuer den
    # AKTUELL laufenden Check (siehe live_progress.py Docstring) - keine
    # DB-Abfrage, kein Ersatz fuer /api/summary nach Abschluss des Laufs.
    if sync.mode() == "receiver":
        remote = sync.remote_progress()
        return remote or dict(live_progress.snapshot(), running=False)
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
    sort: str = Query("price", pattern="^(price|price_per_person|stops|duration|departure|origin|airline|booking_option_price)$"),
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
    if sync.mode() == "receiver":
        sync.request_check()   # der Worker holt den Wunsch beim naechsten Poll ab
        return {"started": True, "note": "Check beim Homeserver angefordert (startet in <= 15 s)"}
    scheduler.trigger_async()
    return {"started": True, "note": "Check laeuft im Hintergrund, /api/status zeigt Fortschritt"}


# --- Worker -> Dashboard (siehe sync.py) ---------------------------------
def _require_sync(token: str | None) -> None:
    if sync.mode() != "receiver":
        raise HTTPException(status_code=404, detail="Sync-Empfang nicht aktiv")
    if not sync.token_ok(token):
        raise HTTPException(status_code=401, detail="ungueltiges Sync-Token")


@router.post("/worker/ingest")
def worker_ingest(payload: dict = Body(...), x_sync_token: str | None = Header(None)):
    _require_sync(x_sync_token)
    s = get_session()
    try:
        new_id = sync.import_run(s, payload)
        alerts = []
        if new_id is not None:
            try:
                alerts = sync.alert_for_run(s, new_id)
            except Exception as exc:  # noqa: BLE001  (Alarm darf den Import nie kippen)
                log.warning("Alarmpruefung nach Import fehlgeschlagen: %s", exc)
    finally:
        s.close()
    return {"imported": new_id is not None, "run_id": new_id, "alerts": alerts}


@router.post("/worker/poll")
def worker_poll(payload: dict = Body(default={}), x_sync_token: str | None = Header(None)):
    _require_sync(x_sync_token)
    return {"requested": sync.receive_poll(payload.get("progress"), payload.get("scheduler"))}
