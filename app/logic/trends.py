"""Preis-Trend-Ampel und historische Zeitreihen.

Vergleicht den aktuellen Preis pro Kategorie mit dem Mittel der letzten
``trend.window_checks`` Snapshots -> "gerade guenstig" / "im Durchschnitt" /
"gerade teuer".
"""
from __future__ import annotations

from statistics import mean

from sqlalchemy import select

from ..config import Config
from ..models import BestSnapshot, HotelOfferRow
from ..sources.hotels.santiburi import TAX_FEE_MULTIPLIER

CATEGORIES = ("flight_total", "hotel_total", "separate_total", "package_total")

_LABEL = {
    "guenstig": "gerade guenstig",
    "durchschnitt": "im Durchschnitt",
    "teuer": "gerade teuer",
    "unbekannt": "zu wenig Daten",
}


def comparable_hotel_price(rows, cfg: Config) -> float | None:
    """Guenstigster VERGLEICHBARER Hotelpreis einer Messung: nur echte Scrapes
    (keine Floor-Richtwerte, keine manuellen Referenzen) fuer den Standard-
    zeitraum, Preis inkl. Steuern/Gebuehren (Santiburi direkt zeigt ihn ohne -
    Schaetzung). Nutzer 26.09.26: die alte Kurve zeigte ~6.800 EUR, das war
    Santiburi ohne Gebuehren und um eine Nacht heruntergerechnet - nicht
    vergleichbar mit dem heutigen Alles-inklusive-Preis."""
    default = (cfg.trip.hotel_checkin.isoformat(), cfg.trip.hotel_checkout.isoformat())
    prices = []
    for r in rows:
        raw = r.raw or {}
        if not r.ok or not r.price_total or r.price_total <= 0 or r.is_reference:
            continue
        if raw.get("estimate") or "Floor" in (raw.get("basis") or ""):
            continue
        if (raw.get("checkin") or default[0], raw.get("checkout") or default[1]) != default:
            continue
        price = raw.get("price_total_incl_fees_estimate")
        if not price and r.source == "santiburi_official":
            price = r.price_total * TAX_FEE_MULTIPLIER
        prices.append(price or r.price_total)
    return round(min(prices), 2) if prices else None


def _hotel_value(session, snap: BestSnapshot, cfg: Config) -> float | None:
    """hotel_total einer Messung fuer Trend/Verlauf - neu berechnet, damit alte
    und neue Messungen vergleichbar sind. Ist der Tagessieger eine manuelle
    Referenz (kein Scrape), bleibt der gespeicherte Wert."""
    hotel = (snap.verdict or {}).get("hotel") or {}
    if hotel.get("is_reference"):
        return snap.hotel_total
    rows = list(session.scalars(select(HotelOfferRow).where(HotelOfferRow.run_id == snap.run_id)))
    return comparable_hotel_price(rows, cfg) or snap.hotel_total


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
        series = [_hotel_value(session, r, cfg) if cat == "hotel_total" else getattr(r, cat)
                  for r in rows]
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
        "hotel_total": [_hotel_value(session, r, cfg) for r in rows],
        "separate_total": [r.separate_total for r in rows],
        "package_total": [r.package_total for r in rows],
        "currency": cfg.trip.currency,
    }
