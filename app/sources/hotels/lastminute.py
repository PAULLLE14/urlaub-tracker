"""lastminute.com - Preis fuer das Santiburi, live per Deep-Link.

Gleiche Masche wie CHECK24/Santiburi/Expedia - Termin + Belegung direkt in
der URL, das Hotel wird per ID an die erste Stelle gepinnt:

    https://www.lastminute.de/s/tsx?dateFrom=2027-05-15&dateTo=2027-05-28
        &pageType=search&searchMode=HO&sort=recommended
        &destination=CTY-280526&highlightHotelId=97501&adults=3

``destination`` = interner Gebiets-Code fuer Koh Samui, ``highlightHotelId``
= interne Hotel-ID (beide einmalig per Suche auf lastminute.de ermittelt).
Gleicher Zimmergroessen-Split wie bei den anderen Live-Quellen.
"""
from __future__ import annotations

import contextlib
import re
from datetime import date
from urllib.parse import urlencode

from ...config import Config
from ...logging_setup import get_logger
from ...offers import HotelOffer
from ..room_split import candidate_allocations, cheapest_allocation
from ..scraper_base import browser_page, dismiss_consent, goto, parse_money, save_screenshot

log = get_logger("source.lastminute")
SOURCE = "lastminute"

BASE = "https://www.lastminute.de/s/tsx"
DESTINATION = "CTY-280526"  # Koh Samui
HOTEL_ID = "97501"          # Santiburi Koh Samui (lastminute-interne ID)

_PRICE_NEAR_NAME = re.compile(
    r"Santiburi Koh Samui.{0,600}?([\d][\d.,]*)\s*€\s*\nGesamtpreis f(?:ü|u)r\s*(\d+)\s*N(?:ä|a)chte",
    re.S,
)


def _url(checkin: date, checkout: date, adults: int) -> str:
    params = {
        "dateFrom": checkin.isoformat(), "dateTo": checkout.isoformat(),
        "pageType": "search", "searchMode": "HO", "sort": "recommended",
        "destination": DESTINATION, "highlightHotelId": HOTEL_ID, "adults": adults,
    }
    return BASE + "?" + urlencode(params)


async def _search_one(cfg: Config, url: str) -> float | None:
    async with browser_page(cfg) as page:
        await goto(page, url)
        await page.wait_for_timeout(1200)
        await dismiss_consent(page)
        with contextlib.suppress(Exception):
            await page.wait_for_function(
                "document.body.innerText.includes('Gesamtpreis') || "
                "document.body.innerText.includes('Ergebnisse gefunden')",
                timeout=20000,
            )
        await page.wait_for_timeout(1500)
        body = ""
        with contextlib.suppress(Exception):
            body = await page.inner_text("body", timeout=6000)
        m = _PRICE_NEAR_NAME.search(body)
        price = parse_money(m.group(1)) if m else None
        if price is None and cfg.sources.scraper.screenshot_on_error:
            await save_screenshot(page, SOURCE)
        return price


async def fetch(cfg: Config) -> HotelOffer:
    t = cfg.trip
    checkin, checkout = t.hotel_checkin, t.hotel_checkout
    nights = (checkout - checkin).days
    allocations = candidate_allocations(t.persons, t.max_persons_per_room)

    offer = HotelOffer(source=SOURCE, ok=False, price_total=None,
                       currency=t.currency, nights=nights, rooms=t.rooms,
                       guests=t.persons, room_desc=t.hotel.room_type_hint)
    if not allocations:
        offer.error = "keine gueltige Zimmeraufteilung (trip.max_persons_per_room pruefen)"
        return offer

    per_size: dict[str, dict] = {}
    sizes = sorted({size for alloc in allocations for size in alloc})
    for size in sizes:
        url = _url(checkin, checkout, size)
        try:
            price = await _search_one(cfg, url)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s: 1 Zimmer/%d Erw. fehlgeschlagen: %s", SOURCE, size, exc)
            per_size[str(size)] = {"url": url, "error": f"{type(exc).__name__}: {exc}"}
            continue
        if price is None:
            log.info("%s: 1 Zimmer/%d Erw. -> kein Preis lesbar", SOURCE, size)
            per_size[str(size)] = {"url": url, "cheapest": None}
        else:
            log.info("%s: 1 Zimmer/%d Erw. -> %.0f %s", SOURCE, size, price, t.currency)
            per_size[str(size)] = {"url": url, "cheapest": price}

    price_per_size = {size: info["cheapest"] for size, info in
                      ((s, per_size.get(str(s), {})) for s in sizes)
                      if info.get("cheapest") is not None}
    best = cheapest_allocation(allocations, price_per_size)

    offer.raw = {
        "candidate_allocations": [{str(k): v for k, v in a.items()} for a in allocations],
        "per_room_size": per_size,
        "basis": ("Live-Summe: je Zimmergroesse einzeln gesucht (1 Zimmer, N "
                  "Erwachsene), guenstigste Kombination aus allen sinnvollen "
                  "Zimmer-Aufteilungen gewaehlt"),
    }
    if per_size:
        offer.deep_link = next(iter(per_size.values())).get("url", "")

    if best:
        counts, total = best
        offer.ok = True
        offer.price_total = round(total, 2)
        offer.per_night = round(total / nights, 2) if nights else None
        offer.rooms = sum(counts.values())
        offer.raw["room_split"] = {str(k): v for k, v in counts.items()}
        log.info("%s: gesamt %.0f %s (%d Naechte, guenstigste Aufteilung %s von %d geprueften)",
                 SOURCE, offer.price_total, t.currency, nights, counts, len(allocations))
    else:
        offer.error = "nicht fuer alle Zimmergroessen ein Preis gefunden"
        log.warning("%s: %s (%s)", SOURCE, offer.error, per_size)
    return offer
