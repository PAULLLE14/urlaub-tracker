"""Worker -> Dashboard-Sync: Suche auf dem Homeserver, Anzeige auf dem Hetzner-Server.

Hintergrund: Google Hotels/Santiburi zeigen auf der Rechenzentrums-IP des
Hetzner-Servers nur einen Bruchteil der Anbieter bzw. sperren ganz, von der
deutschen Heim-IP aus kommt alles. Daher zwei Rollen (gleicher Code):

* **worker** (Homeserver): ``SYNC_PUSH_URL`` + ``SYNC_TOKEN`` gesetzt. Sucht,
  schreibt in die eigene DB und schickt jeden fertigen Lauf per HTTP an den
  Empfaenger. Fragt dort alle ~15 s nach (Heartbeat + Live-Fortschritt hoch,
  "Jetzt pruefen"-Wunsch runter). Verschickt auch die Alarme.
* **receiver** (Hetzner): nur ``SYNC_TOKEN`` gesetzt, ``schedule.enabled: false``.
  Nimmt Laeufe an (``/api/worker/ingest``), zeigt sie an und merkt sich den
  "Jetzt pruefen"-Wunsch fuer den Worker. Der Worker muss den Empfaenger
  erreichen koennen (ausgehend), umgekehrt ist nichts noetig (NAT egal).

Ohne ``SYNC_TOKEN`` ist alles aus und der Tracker verhaelt sich wie vorher.
"""
from __future__ import annotations

import hmac
import threading
import time
from datetime import datetime

import httpx
from sqlalchemy import DateTime, select

from .config import get_secrets
from .database import get_session
from .logging_setup import get_logger
from .models import BestSnapshot, CheckRun, FlightOfferRow, HotelOfferRow, PackageOfferRow

log = get_logger("sync")

_CHILDREN = {"flights": FlightOfferRow, "hotels": HotelOfferRow, "packages": PackageOfferRow}
_POLL_SECONDS = 15
_FRESH_SECONDS = 90

# --- Empfaenger-Zustand (nur im Speicher) ---------------------------------
_lock = threading.Lock()
_remote: dict = {"progress": None, "scheduler": None, "seen": 0.0, "requested": False}


def mode() -> str:
    sec = get_secrets()
    if not sec.sync_token:
        return "off"
    return "worker" if sec.sync_push_url else "receiver"


def token_ok(given: str | None) -> bool:
    sec = get_secrets()
    return bool(sec.sync_token and given and hmac.compare_digest(given, sec.sync_token))


# --- (De-)Serialisierung ---------------------------------------------------
def _dump(obj, skip: set[str]) -> dict:
    out = {}
    for col in obj.__table__.columns:
        if col.key in skip:
            continue
        val = getattr(obj, col.key)
        out[col.key] = val.isoformat() if isinstance(val, datetime) else val
    return out


def _load(model, data: dict, extra: dict):
    kwargs = dict(extra)
    for col in model.__table__.columns:
        if col.key in data and col.key not in ("id", "run_id"):
            val = data[col.key]
            if isinstance(col.type, DateTime) and isinstance(val, str):
                val = datetime.fromisoformat(val)
            kwargs[col.key] = val
    return model(**kwargs)


def export_run(session, run_id: int) -> dict:
    run = session.get(CheckRun, run_id)
    if run is None:
        raise ValueError(f"CheckRun {run_id} nicht gefunden")
    payload = {"run": _dump(run, {"id"})}
    for key, model in _CHILDREN.items():
        rows = session.scalars(select(model).where(model.run_id == run_id).order_by(model.id))
        payload[key] = [_dump(r, {"run_id"}) for r in rows]
    snap = session.scalar(select(BestSnapshot).where(BestSnapshot.run_id == run_id))
    payload["snapshot"] = _dump(snap, {"run_id"}) if snap else None
    return payload


def import_run(session, payload: dict) -> int | None:
    """Legt den Lauf mit neuen IDs an; Snapshot-Verweise werden umgebogen.
    Idempotent: gleicher ``started_at`` -> wird uebersprungen (None)."""
    started = datetime.fromisoformat(payload["run"]["started_at"])
    if session.scalar(select(CheckRun.id).where(CheckRun.started_at == started)):
        return None
    run = _load(CheckRun, payload["run"], {})
    session.add(run)
    session.flush()
    idmap: dict[str, dict[int, object]] = {}
    for key, model in _CHILDREN.items():
        idmap[key] = {}
        for d in payload.get(key, []):
            row = _load(model, d, {"run_id": run.id})
            session.add(row)
            idmap[key][d["id"]] = row
    session.flush()
    snap = payload.get("snapshot")
    if snap:
        def new_id(kind: str, old):
            row = idmap[kind].get(old) if old is not None else None
            return row.id if row is not None else None

        s = _load(BestSnapshot, snap, {"run_id": run.id})
        s.flight_out_id = new_id("flights", snap.get("flight_out_id"))
        s.flight_ret_id = new_id("flights", snap.get("flight_ret_id"))
        s.hotel_id = new_id("hotels", snap.get("hotel_id"))
        s.package_id = new_id("packages", snap.get("package_id"))
        session.add(s)
    session.commit()
    return run.id


# --- Worker-Seite ----------------------------------------------------------
def _url(path: str) -> str:
    return get_secrets().sync_push_url.rstrip("/") + path


def _headers() -> dict:
    return {"X-Sync-Token": get_secrets().sync_token or ""}


def push_run(run_id: int) -> bool:
    """Schickt einen fertigen Lauf an den Empfaenger (3 Versuche)."""
    if mode() != "worker":
        return False
    session = get_session()
    try:
        payload = export_run(session, run_id)
    finally:
        session.close()
    for attempt in range(1, 4):
        try:
            r = httpx.post(_url("/api/worker/ingest"), json=payload, headers=_headers(), timeout=60)
            r.raise_for_status()
            log.info("Lauf #%s an Dashboard gesendet (%s)", run_id, r.json())
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("Sync Lauf #%s Versuch %d/3 fehlgeschlagen: %s", run_id, attempt, exc)
            time.sleep(5 * attempt)
    return False


def _worker_loop() -> None:
    from . import live_progress, scheduler

    while True:
        try:
            r = httpx.post(
                _url("/api/worker/poll"), headers=_headers(), timeout=20,
                json={"progress": live_progress.snapshot(), "scheduler": scheduler.info()},
            )
            r.raise_for_status()
            if r.json().get("requested") and not live_progress.snapshot().get("running"):
                log.info("Manueller Check vom Dashboard angefordert")
                scheduler.trigger_async()
        except Exception as exc:  # noqa: BLE001
            log.debug("Worker-Poll fehlgeschlagen: %s", exc)
        time.sleep(_POLL_SECONDS)


def start() -> None:
    if mode() == "worker":
        threading.Thread(target=_worker_loop, name="sync-worker", daemon=True).start()
        log.info("Sync-Worker aktiv -> %s", get_secrets().sync_push_url)
    elif mode() == "receiver":
        log.info("Sync-Empfaenger aktiv (Suche laeuft auf dem Worker)")


# --- Empfaenger-Seite ------------------------------------------------------
def receive_poll(progress: dict | None, sched: dict | None) -> bool:
    with _lock:
        _remote.update(progress=progress, scheduler=sched, seen=time.time())
        requested, _remote["requested"] = _remote["requested"], False
    return requested


def request_check() -> None:
    with _lock:
        _remote["requested"] = True


def remote_progress() -> dict | None:
    """Live-Fortschritt vom Worker, solange dessen Heartbeat frisch ist."""
    with _lock:
        if time.time() - _remote["seen"] < _FRESH_SECONDS:
            return _remote["progress"]
    return None


def remote_scheduler_info() -> dict:
    with _lock:
        fresh = time.time() - _remote["seen"] < _FRESH_SECONDS
        info = dict(_remote["scheduler"] or {})
    info["running"] = fresh and bool(info.get("running"))
    info["worker_online"] = fresh
    return info
