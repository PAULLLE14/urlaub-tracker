"""Preis-Trend-Ampel und historische Zeitreihen.

Vergleicht den aktuellen Preis pro Kategorie mit dem Mittel der letzten
``trend.window_checks`` Snapshots -> "gerade guenstig" / "im Durchschnitt" /
"gerade teuer".
"""
from __future__ import annotations

from statistics import mean

from sqlalchemy import select

from ..config import Config
from ..models import BestSnapshot

CATEGORIES = ("flight_total", "hotel_total", "separate_total", "package_total")

_LABEL = {
    "guenstig": "gerade guenstig",
    "durchschnitt": "im Durchschnitt",
    "teuer": "gerade teuer",
    "unbekannt": "zu wenig Daten",
}


def classify(current: float | None, history: list[float], cfg: Config) -> dict:
    hist = [h for h in history if h is not None and h > 0]
    if current is None or current <= 0 or len(hist) < 3:
        return {"state": "unbekannt", "label": _LABEL["unbekannt"],
                "current": current, "avg": (round(mean(hist), 2) if hist else None),
                "pct_vs_avg": None, "n": len(hist)}

    avg = mean(hist)
    pct = (current - avg) / avg * 100.0
    if pct <= -cfg.trend.cheap_threshold_pct:
        state = "guenstig"
    elif pct >= cfg.trend.expensive_threshold_pct:
        state = "teuer"
    else:
        state = "durchschnitt"
    return {"state": state, "label": _LABEL[state], "current": round(current, 2),
            "avg": round(avg, 2), "pct_vs_avg": round(pct, 1), "n": len(hist)}


def compute_trends(session, cfg: Config) -> dict:
    rows = list(session.scalars(
        select(BestSnapshot).order_by(BestSnapshot.created_at.desc())
        .limit(cfg.trend.window_checks)
    ))
    rows = list(reversed(rows))  # aelteste zuerst
    out: dict[str, dict] = {}
    for cat in CATEGORIES:
        series = [getattr(r, cat) for r in rows]
        current = series[-1] if series else None
        prior = series[:-1] if len(series) > 1 else []
        out[cat] = classify(current, prior, cfg)
    return out


def history_series(session, cfg: Config, limit: int = 500) -> dict:
    rows = list(session.scalars(
        select(BestSnapshot).order_by(BestSnapshot.created_at.desc()).limit(limit)
    ))
    rows = list(reversed(rows))
    return {
        "t": [r.created_at.isoformat() for r in rows],
        "flight_total": [r.flight_total for r in rows],
        "hotel_total": [r.hotel_total for r in rows],
        "separate_total": [r.separate_total for r in rows],
        "package_total": [r.package_total for r in rows],
        "currency": cfg.trip.currency,
    }
