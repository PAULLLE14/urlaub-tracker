"""Expedia - Preis fuer das Santiburi, live per Deep-Link.

Wie CHECK24/Santiburi direkt: Belegung + Termin stecken direkt in der
Such-URL, kein Formular noetig. ``propertyId``/``hotelId``/``selected``
pinnen genau dieses Hotel als ersten Treffer:

    https://www.expedia.de/Hotel-Search?destination=Santiburi+Koh+Samui
        &propertyId=1823702&hotelId=1823702&selected=1823702
        &startDate=2027-05-15&endDate=2027-05-28&adults=3&rooms=1

Der Nutzer bekommt hier ueber ein Corporate-Programm zusaetzlich Rabatt -
der ist NICHT eingerechnet (kein Login/Code automatisiert, siehe
Sicherheitsregeln), sondern muss manuell auf den hier gezeigten oeffentlichen
Preis angewendet werden.

Gleicher Zimmergroessen-Split wie bei CHECK24/Santiburi (``room_split``).
"""
from __future__ import annotations

import contextlib
import re
from datetime import date
from urllib.parse import urlencode

from ...config import Config
from ...logging_setup import get_logger
from ...offers import HotelOffer
from ..room_split import room_split
from ..scraper_base import browser_page, dismiss_consent, goto, parse_money, save_screenshot

log = get_logger("source.expedia")
SOURCE = "expedia"

BASE = "https://www.expedia.de/Hotel-Search"
PROPERTY_ID = "1823702"  # Santiburi Koh Samui (Expedia-interne ID)

_PRICE_NEAR_NAME = re.compile(
    r"Santiburi Koh Samui.{0,500}?([\d][\d.,]*)\s*€\s*\nf(?:ü|u)r\s*(\d+)\s*Zimmer,\s*(\d+)\s*N(?:ä|a)chte",
    re.S,
)


def _url(checkin: date, checkout: date, adults: int) -> str:
    params = {
        "destination": "Santiburi Koh Samui, Ko Samui, Thailand",
        "propertyId": PROPERTY_ID, "hotelId": PROPERTY_ID, "selected": PROPERTY_ID,
        "startDate": checkin.isoformat(), "endDate": checkout.isoformat(),
        "adults": adults, "rooms": 1,
    }
    return BASE + "?" + urlencode(params)


class ExpediaBlocked(RuntimeError):
    """Expedias Bot-Erkennung zeigt einen Slider-Captcha. Wird NICHT geloest
    (verboten) - nur erkannt und als klarer Fehler gemeldet."""


async def _search_one(cfg: Config, url: str) -> float | None:
    async with browser_page(cfg) as page:
        await goto(page, url)
        await page.wait_for_timeout(1200)
        await dismiss_consent(page)
        with contextlib.suppress(Exception):
            await page.wait_for_function(
                "document.body.innerText.includes('Gesamtpreis') || "
                "document.body.innerText.includes('Nächte') || "
                "document.body.innerText.includes('keine Unterk') || "
                "document.body.innerText.includes('menschliche Seite')",
                timeout=20000,
            )
        await page.wait_for_timeout(1200)
        body = ""
        with contextlib.suppress(Exception):
            body = await page.inner_text("body", timeout=6000)
        if "menschliche Seite" in body or "Nach rechts schieben" in body:
            if cfg.sources.scraper.screenshot_on_error:
                await save_screenshot(page, SOURCE)
            raise ExpediaBlocked("Slider-Captcha (Bot-Erkennung) - wird nicht geloest")
        m = _PRICE_NEAR_NAME.search(body)
        price = parse_money(m.group(1)) if m else None
        if price is None and cfg.sources.scraper.screenshot_on_error:
            await save_screenshot(page, SOURCE)
        return price


async def fetch(cfg: Config) -> HotelOffer:
    t = cfg.trip
    checkin, checkout = t.hotel_checkin, t.hotel_checkout
    nights = (checkout - checkin).days
    counts = room_split(t.persons, t.rooms, t.max_persons_per_room)

    offer = HotelOffer(source=SOURCE, ok=False, price_total=None,
                       currency=t.currency, nights=nights, rooms=t.rooms,
                       guests=t.persons, room_desc=t.hotel.room_type_hint)
    if not counts:
        offer.error = "keine gueltige Zimmeraufteilung (trip.rooms/persons pruefen)"
        return offer

    per_size: dict[str, dict] = {}
    for size in sorted(counts):
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

    total, complete = 0.0, True
    for size, cnt in counts.items():
        price = per_size.get(str(size), {}).get("cheapest")
        if price is None:
            complete = False
        else:
            total += price * cnt

    offer.raw = {
        "room_split": {str(k): v for k, v in counts.items()},
        "per_room_size": per_size,
        "basis": ("Live-Summe: je Zimmergroesse einzeln gesucht (1 Zimmer, N "
                  "Erwachsene), guenstigstes Angebot x Anzahl Zimmer dieser Groesse. "
                  "Ohne Corporate-Rabatt (manuell anwenden)."),
    }
    if per_size:
        offer.deep_link = next(iter(per_size.values())).get("url", "")

    if complete:
        offer.ok = True
        offer.price_total = round(total, 2)
        offer.per_night = round(total / nights, 2) if nights else None
        log.info("%s: gesamt %.0f %s (%d Naechte, Aufteilung %s)",
                 SOURCE, offer.price_total, t.currency, nights, counts)
    else:
        blocked = any("ExpediaBlocked" in str(v.get("error", "")) for v in per_size.values())
        offer.error = ("Expedia zeigt einen Bot-Erkennungs-Captcha (Slider) - wird nicht "
                       "automatisiert geloest. Bitte den Deep-Link manuell oeffnen."
                       if blocked else "nicht fuer alle Zimmergroessen ein Preis gefunden")
        log.warning("%s: %s (%s)", SOURCE, offer.error, per_size)
    return offer
