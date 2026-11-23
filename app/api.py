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


@router.get("/guarantee")
def guarantee_status():
    s = get_session()
    try:
        return queries.guarantee_payload(s, get_config())
    finally:
        s.close()


@router.get("/links")
def links():
    """Fertige Google-Hotels-Suche (Santiburi, gebuchter Zeitraum, 2 Personen)."""
    from .sources.hotels.google_hotels import _url_for
    from .sources.hotels import hotel_date_combos

    cfg = get_config()
    checkin, checkout = hotel_date_combos(cfg)[0]
    return {"google_hotels": _url_for(cfg, checkin, checkout, 2)}


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


# --- Browser-Erweiterung -------------------------------------------------
import hmac as _hmac
from datetime import datetime as _dt, timedelta as _td, timezone as _tz


def _ext_auth(token: str | None) -> None:
    want = get_secrets().ext_token
    if not (want and token and _hmac.compare_digest(token, want)):
        raise HTTPException(status_code=401, detail="ungueltiges Token")


@router.post("/ext/ingest")
def ext_ingest(payload: dict = Body(...), x_ext_token: str | None = Header(None)):
    """Preise aus der persoenlichen Google-Hotels-Ansicht (Browser-Erweiterung)."""
    from sqlalchemy import select

    from .models import BrowserMeasurement

    _ext_auth(x_ext_token)
    cfg = get_config()
    t = cfg.trip
    adults = int(payload.get("adults") or 0)
    ci, co = str(payload.get("checkin") or ""), str(payload.get("checkout") or "")
    if (ci, co) != (t.hotel_checkin.isoformat(), t.hotel_checkout.isoformat()) or adults not in (2, 3):
        return {"stored": False, "reason": "nicht der gebuchte Zeitraum bzw. keine 2/3 Gaeste"}
    nights = (t.hotel_checkout - t.hotel_checkin).days
    providers = []
    for p in (payload.get("providers") or [])[:80]:
        try:
            name, per_night, total = str(p["name"])[:40], float(p["per_night"]), float(p["total"])
        except (KeyError, TypeError, ValueError):
            continue
        # Plausibilitaet: Santiburi, 1 Zimmer, ~13 Naechte
        if not (30 <= per_night <= 2500) or not (per_night * nights * 0.7 <= total <= per_night * nights * 1.3):
            continue
        providers.append({"name": name, "per_night": per_night, "total": total,
                          "url": str(p.get("url") or "")[:1500]})
    if not providers:
        return {"stored": False, "reason": "keine plausiblen Preise"}
    s = get_session()
    try:
        last = s.scalar(select(BrowserMeasurement).where(
            BrowserMeasurement.adults == adults, BrowserMeasurement.checkin == ci)
            .order_by(BrowserMeasurement.captured_at.desc()).limit(1))
        now = _dt.now(_tz.utc)
        if last and last.providers == providers:
            last.captured_at = now        # unveraendert: nur Zeitstempel auffrischen
        else:
            s.add(BrowserMeasurement(captured_at=now, checkin=ci, checkout=co,
                                     adults=adults, providers=providers))
        s.commit()
    finally:
        s.close()
    log.info("Browser-Erweiterung: %d Anbieter fuer %d Gaeste gespeichert", len(providers), adults)
    return {"stored": True, "providers": len(providers)}


@router.get("/ext/latest")
def ext_latest(max_age_hours: float = 24, x_sync_token: str | None = Header(None)):
    """Neueste Browser-Messung je Gaestezahl (fuer den Worker)."""
    from sqlalchemy import select

    from .models import BrowserMeasurement

    if not sync.token_ok(x_sync_token):
        raise HTTPException(status_code=401, detail="ungueltiges Sync-Token")
    since = _dt.now(_tz.utc) - _td(hours=max_age_hours)
    out = []
    s = get_session()
    try:
        for adults in (2, 3):
            m = s.scalar(select(BrowserMeasurement).where(
                BrowserMeasurement.adults == adults, BrowserMeasurement.captured_at >= since)
                .order_by(BrowserMeasurement.captured_at.desc()).limit(1))
            if m:
                cap = m.captured_at if m.captured_at.tzinfo else m.captured_at.replace(tzinfo=_tz.utc)
                out.append({"captured_at": cap.isoformat(), "checkin": m.checkin, "checkout": m.checkout,
                            "adults": m.adults, "providers": m.providers})
    finally:
        s.close()
    return {"measurements": out}


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
