"""Pauschalreisen (Flug + Hotel) live via CHECK24.

Gleicher Deep-Link-Trick wie beim CHECK24-Hotelvergleich (siehe
``app/sources/hotels/check24.py``), nur auf urlaub.check24.de:

    https://urlaub.check24.de/suche/hotel?areaId=<AreaId>&hotelId=<HotelId>
        &airport=STR,MUC,FRA&transportType=flight
        &departureDate=2027-05-14&returnDate=2027-05-28&days=exact
        &roomAllocation=A-A-A&pageArea=package&dhs=<HotelId>&extendedSearch=1

``roomAllocation=A-A-A`` = 1 Zimmer, 3 Erwachsene (Bindestrich-getrennt -
anders als beim Hotelvergleich mit ``[A|A|A]``). Wie beim Hotelvergleich wird
je Zimmergroesse einzeln gesucht (``room_split``) und aufsummiert; das
Ergebnis enthaelt bereits die Fluege.

ZRH wird nicht mitgesucht: CHECK24.de bietet nur deutsche Abflughaefen an
(kein Schweizer Pendant in dieser Suche).

``check24_package_hotel_id`` / ``check24_package_area_id`` sind eine ANDERE
ID als beim reinen Hotelvergleich - einmalig ueber die Pauschalreise-Suche
auf urlaub.check24.de ermitteln (Ergebnis-URL nach der Suche).
"""
from __future__ import annotations

import asyncio
import contextlib
import random
import re
from datetime import date
from urllib.parse import urlencode

from ...config import Config
from ...logging_setup import get_logger
from ...offers import PackageOffer
from ..room_split import candidate_allocations, cheapest_allocation
from ..scraper_base import browser_page, dismiss_consent, goto, parse_money, save_screenshot

log = get_logger("source.check24_package")
SOURCE = "check24_package"

BASE = "https://urlaub.check24.de/suche/hotel"


def _occupancy_param(adults: int) -> str:
    return "-".join(["A"] * adults)


def _url(area_id: str, hotel_id: str, airports: list[str], checkin: date,
        checkout: date, adults: int) -> str:
    params = {
        "areaId": area_id, "hotelId": hotel_id,
        "airport": ",".join(airports), "transportType": "flight",
        "departureDate": checkin.isoformat(), "returnDate": checkout.isoformat(),
        "days": "exact", "roomAllocation": _occupancy_param(adults),
        "pageArea": "package", "dhs": hotel_id, "extendedSearch": 1,
    }
    return BASE + "?" + urlencode(params)


def _card_price(text: str, hotel_name: str) -> float | None:
    """Preis der (angepinnten) ersten Ergebniskarte fuer genau dieses Hotel."""
    m = re.search(
        re.escape(hotel_name) + r".{0,400}?(\d+)\s*Tage\s*\|\s*(\d+)\s*Pers\.\s*\|"
        r"\s*Flug\s*\+\s*Unterkunft\s*\n\s*([\d.,]+)\s*€",
        text, re.S,
    )
    if not m:
        return None
    return parse_money(m.group(3))


async def _search_one(cfg: Config, url: str, hotel_name: str) -> float | None:
    async with browser_page(cfg) as page:
        await goto(page, url)
        await page.wait_for_timeout(1200)
        with contextlib.suppress(Exception):
            await page.keyboard.press("Escape")  # Promo-Layer schliessen
        await dismiss_consent(page)
        with contextlib.suppress(Exception):
            await page.wait_for_function(
                "document.body.innerText.includes('Flug + Unterkunft') || "
                "document.body.innerText.includes('keine Ergebnisse') || "
                "document.body.innerText.includes('0 Hotels')",
                timeout=25000,
            )
        await page.wait_for_timeout(1200)
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=6000)
        body = ""
        with contextlib.suppress(Exception):
            body = await page.inner_text("body", timeout=6000)
        price = _card_price(body, hotel_name)
        if price is None and cfg.sources.scraper.screenshot_on_error:
            await save_screenshot(page, SOURCE)
        return price


async def fetch(cfg: Config) -> list[PackageOffer]:
    t = cfg.trip
    area_id = t.hotel.check24_package_area_id
    hotel_id = t.hotel.check24_package_hotel_id
    checkin, checkout = t.outbound_dates[0], t.return_dates[0]
    nights = (checkout - checkin).days
    # Alle sinnvollen Zimmer-Aufteilungen vergleichen statt einer festen
    # (13.09.26 Nutzervorgabe) - siehe hotels/check24.py fuer dasselbe Muster.
    allocations = candidate_allocations(t.persons, t.max_persons_per_room)
    # CHECK24.de kennt nur deutsche Abflughaefen - ZRH (Schweiz) faellt raus.
    airports = [a for a in t.origin_airports if a != "ZRH"]

    base = dict(source=SOURCE, currency=t.currency, operator="CHECK24",
                hotel_name=t.hotel.name, nights=nights, persons=t.persons,
                dep_airport="/".join(airports))

    if not area_id or not hotel_id:
        return [PackageOffer(ok=False, price_total=None,
                             error=("hotel.check24_package_area_id / "
                                   "_hotel_id nicht gesetzt - einmalig auf "
                                   "urlaub.check24.de (Pauschalreisen) suchen "
                                   "und aus der Ergebnis-URL eintragen"),
                             **base)]
    if not allocations or not airports:
        return [PackageOffer(ok=False, price_total=None,
                             error="keine Zimmeraufteilung oder keine dt. Abflughaefen konfiguriert",
                             **base)]

    lo, hi = cfg.sources.packages.check24_delay_seconds
    per_size: dict[str, dict] = {}
    sizes = sorted({size for alloc in allocations for size in alloc})
    for i, size in enumerate(sizes):
        url = _url(area_id, hotel_id, airports, checkin, checkout, size)
        try:
            price = await _search_one(cfg, url, t.hotel.name)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s: 1 Zimmer/%d Erw. fehlgeschlagen: %s", SOURCE, size, exc)
            per_size[str(size)] = {"url": url, "error": f"{type(exc).__name__}: {exc}"}
            continue
        if price is None:
            log.info("%s: 1 Zimmer/%d Erw. -> kein Paketpreis lesbar", SOURCE, size)
            per_size[str(size)] = {"url": url, "price": None}
        else:
            log.info("%s: 1 Zimmer/%d Erw. -> %.0f %s (Flug+Hotel)",
                     SOURCE, size, price, t.currency)
            per_size[str(size)] = {"url": url, "price": price}
        if i < len(sizes) - 1:
            await asyncio.sleep(random.uniform(lo, hi))

    price_per_size = {size: info["price"] for size, info in
                      ((s, per_size.get(str(s), {})) for s in sizes)
                      if info.get("price") is not None}
    best = cheapest_allocation(allocations, price_per_size)

    raw = {
        "candidate_allocations": [{str(k): v for k, v in a.items()} for a in allocations],
        "per_room_size": per_size,
        "airports_used": airports,
        "basis": ("Live-Summe: je Zimmergroesse einzeln gesucht (1 Zimmer, N "
                  "Erwachsene, Flug+Hotel als Paket), guenstigste Kombination "
                  "aus allen sinnvollen Zimmer-Aufteilungen gewaehlt"),
    }
    deep_link = next(iter(per_size.values()), {}).get("url", "")

    if best:
        counts, total = best
        raw["room_split"] = {str(k): v for k, v in counts.items()}
        log.info("%s: gesamt %.0f %s (guenstigste Aufteilung %s von %d geprueften)",
                 SOURCE, total, t.currency, counts, len(allocations))
        return [PackageOffer(ok=True, price_total=round(total, 2),
                             deep_link=deep_link, raw=raw, **base)]
    return [PackageOffer(ok=False, price_total=None,
                         error="fuer keine Zimmeraufteilung durchgehend ein Paketpreis gefunden",
                         deep_link=deep_link, raw=raw, **base)]
