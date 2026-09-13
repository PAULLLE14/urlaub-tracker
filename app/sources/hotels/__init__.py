"""Hotel-Quellen buendeln.

Live-Deep-Link-Quellen (Playwright, je Zimmergroesse einzeln gesucht):
CHECK24, Santiburi direkt, Expedia, lastminute.com. Dazu Google Hotels
(synchron, ein Request, Floor-Richtwert). Booking als Fallback, fragil,
per Default aus.
"""
from __future__ import annotations

import asyncio

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
    return out


def collect_hotels(cfg: Config, proxy: str | None = None) -> tuple[list[HotelOffer], dict]:
    hc = cfg.sources.hotels
    offers: list[HotelOffer] = []

    # 1) Primaerquelle: Google Hotels (synchron, kein Browser)
    if hc.google_hotels.enabled:
        try:
            offers.append(google_hotels.fetch(cfg, proxy=proxy))
        except Exception as exc:  # noqa: BLE001
            offers.append(HotelOffer(source="google_hotels", ok=False,
                                     price_total=None, currency=cfg.trip.currency,
                                     error=f"{type(exc).__name__}: {exc}"))

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
