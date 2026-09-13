"""APScheduler-Wrapper: Preis-Check alle N Stunden im Hintergrund.

Laeuft im selben Prozess wie FastAPI (BackgroundScheduler, eigener Thread).
Auf dem Server kann alternativ ein System-Cron ``python -m app.check`` rufen -
dann ``schedule.enabled: false`` setzen.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .config import get_config
from .logging_setup import get_logger

log = get_logger("scheduler")

_scheduler: BackgroundScheduler | None = None
_lock = threading.Lock()
_JOB_ID = "price_check"


def _job() -> None:
    from .collector import run_check

    with _lock:  # nie zwei Checks parallel
        try:
            run_check(trigger="schedule")
        except Exception as exc:  # noqa: BLE001
            log.exception("Geplanter Check fehlgeschlagen: %s", exc)


def start() -> None:
    global _scheduler
    cfg = get_config()
    if not cfg.schedule.enabled:
        log.info("Scheduler deaktiviert (schedule.enabled=false)")
        return
    if _scheduler:
        return
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        _job, IntervalTrigger(hours=cfg.schedule.every_hours),
        id=_JOB_ID, jitter=cfg.schedule.jitter_seconds,
        max_instances=1, coalesce=True,
        next_run_time=datetime.now(timezone.utc) if cfg.schedule.run_on_start else None,
    )
    _scheduler.start()
    log.info("Scheduler gestartet: alle %.1f h (jitter %ds)",
             cfg.schedule.every_hours, cfg.schedule.jitter_seconds)


def shutdown() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def info() -> dict:
    if not _scheduler:
        return {"running": False}
    job = _scheduler.get_job(_JOB_ID)
    return {
        "running": True,
        "next_run": job.next_run_time.isoformat() if job and job.next_run_time else None,
        "interval_hours": get_config().schedule.every_hours,
    }


def trigger_async() -> None:
    """Sofort-Check in einem Worker-Thread (fuer POST /api/check/run)."""
    threading.Thread(target=_job, name="manual-check", daemon=True).start()
