"""Hotel-Quellen buendeln.

Live-Deep-Link-Quellen (Playwright, je Zimmergroesse einzeln gesucht):
CHECK24, Santiburi direkt, Expedia, lastminute.com. Dazu Google Hotels
(synchron, ein Request, Floor-Richtwert). Booking als Fallback, fragil,
per Default aus.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

from ...config import Config
from ...logging_setup import get_logger
from ...offers import HotelOffer
from ..scraper_base import playwright_available
from . import booking, check24, expedia, google_hotels, lastminute, santiburi

log = get_logger("source.hotels")

_PLAYWRIGHT_ADAPTERS = {
    "check24": check24,
    "santiburi_official": santiburi,
    "expedia": expedia,
    "lastminute": lastminute,
    "booking": booking,
}


# Nutzer 19.09.26: ein Flug 14.->28. bringt nur dann etwas, wenn das Hotel
# dazu passt (Ankunft +1 Tag). Deshalb wird fuer diese Quellen jede
# Hotel-Datumskombination echt abgefragt statt eine Nacht hoch-/runterzurechnen.
# Santiburi direkt bewusst NICHT hier: die Buchungsmaschine sitzt hinter einer
# Bot-Abfrage (Imperva) - nur der gebuchte Zeitraum wird dort abgefragt.
_MATRIX_SOURCES = ("check24",)


def hotel_date_combos(cfg: Config) -> list[tuple[date, date]]:
    """(Checkin, Checkout) je Flug-Datumspaar: Checkin = Abflug + 1 Tag. Bei
    bereits gebuchtem Flug: die Kombination des gebuchten Flugs zuerst; die
    uebrigen bleiben als Vergleich (was kostet eine Nacht mehr/weniger)."""
    return sorted({(d + timedelta(days=1), r) for d, r in cfg.trip.rt_date_pairs()})


def _tag_dates(offer: HotelOffer, checkin: date, checkout: date) -> HotelOffer:
    offer.raw = {**(offer.raw or {}), "checkin": checkin.isoformat(),
                 "checkout": checkout.isoformat()}
    return offer


def _cfg_for_dates(cfg: Config, checkin: date, checkout: date) -> Config:
    trip = cfg.trip.model_copy(update={"hotel_checkin": checkin, "hotel_checkout": checkout,
                                       "nights": (checkout - checkin).days})
    return cfg.model_copy(update={"trip": trip})


async def _collect_matrix(cfg: Config, names: list[str]) -> list[HotelOffer]:
    default = (cfg.trip.hotel_checkin, cfg.trip.hotel_checkout)
    combos = [c for c in hotel_date_combos(cfg) if c != default]

    async def one_source(name: str) -> list[HotelOffer]:
        out: list[HotelOffer] = []
        for ci, co in combos:  # nacheinander: nicht mehrere Browser je Quelle parallel
            try:
                off = await _PLAYWRIGHT_ADAPTERS[name].fetch(_cfg_for_dates(cfg, ci, co))
            except Exception as exc:  # noqa: BLE001
                off = HotelOffer(source=name, ok=False, price_total=None,
                                 currency=cfg.trip.currency,
                                 error=f"{type(exc).__name__}: {exc}")
            out.append(_tag_dates(off, ci, co))
        return out

    results = await asyncio.gather(*(one_source(n) for n in names))
    return [o for chunk in results for o in chunk]


async def _collect_playwright(cfg: Config, names: list[str]) -> list[HotelOffer]:
    tasks = [_PLAYWRIGHT_ADAPTERS[n].fetch(cfg) for n in names]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out: list[HotelOffer] = []
    for name, res in zip(names, results):
        if isinstance(res, Exception):
            out.append(HotelOffer(source=name, ok=False, price_total=None,
                                  currency=cfg.trip.currency,
                                  error=f"{type(res).__name__}: {res}"))
        else:
            out.append(res)
    default = (cfg.trip.hotel_checkin, cfg.trip.hotel_checkout)
    out = [_tag_dates(o, *default) for o in out]
    matrix_names = [n for n in names if n in _MATRIX_SOURCES]
    if matrix_names:
        out += await _collect_matrix(cfg, matrix_names)
    return out


def collect_hotels(cfg: Config, proxy: str | None = None) -> tuple[list[HotelOffer], dict]:
    hc = cfg.sources.hotels
    offers: list[HotelOffer] = []

    # 1) Google Hotels: alle Anbieter je Zeitraum (HTTP, kein Browser) - der
    # eigentliche Vergleich (Booking, Expedia, Agoda, Trip.com, CHECK24, TUI ...).
    if hc.google_hotels.enabled:
        for ci, co in hotel_date_combos(cfg):
            try:
                offers += google_hotels.fetch_providers(cfg, ci, co, proxy=proxy)
            except Exception as exc:  # noqa: BLE001
                offers.append(_tag_dates(HotelOffer(
                    source="google_hotels", ok=False, price_total=None,
                    currency=cfg.trip.currency,
                    error=f"{type(exc).__name__}: {exc}"), ci, co))

    # 2) Optionale Playwright-Fallbacks
    pw_names = [n for n in _PLAYWRIGHT_ADAPTERS if getattr(hc, n).enabled]
    if pw_names:
        ok, err = playwright_available()
        if not ok:
            log.warning("Playwright-Hotelquellen uebersprungen: %s", err)
        else:
            try:
                offers += asyncio.run(_collect_playwright(cfg, pw_names))
            except RuntimeError:
                loop = asyncio.new_event_loop()
                try:
                    offers += loop.run_until_complete(_collect_playwright(cfg, pw_names))
                finally:
                    loop.close()

    good = sum(1 for o in offers if o.ok)
    health = {
        "ok": good > 0,
        "count": good,
        "attempted": len(offers),
        "error": "; ".join(f"{o.source}: {o.error}" for o in offers
                           if not o.ok and o.error) or "",
    }
    return offers, health
