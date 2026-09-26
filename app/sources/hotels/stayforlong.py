"""Stayforlong - Santiburi mit ALLEN Zimmern in EINER Suche (Playwright).

Nutzer-Fund 26.09.26 (Screenshot): Stayforlong zeigte fuer 4 Zimmer x 2
Erwachsene, 15.-28.05.2027 8.167 EUR inkl. Steuern/Gebuehren (Flash-Deal
"-33% Heute", nicht erstattbar) - guenstiger als jeder von Google Hotels
gelieferte Anbieter. Die Seite nimmt die komplette Zimmeraufteilung per URL
(``adults=2,2,2,2``, ``children=!!!``) und zeigt Gesamtpreise fuer ALLE Zimmer,
deshalb hier je Zimmeraufteilung EINE Suche statt je Zimmergroesse.
"""
from __future__ import annotations

import contextlib
import re
from datetime import date
from urllib.parse import urlencode

from ...config import Config
from ...logging_setup import get_logger
from ...offers import HotelOffer
from ..room_split import candidate_allocations, explicit_allocations
from ..scraper_base import browser_page, dismiss_consent, goto, parse_money

log = get_logger("source.stayforlong")
SOURCE = "stayforlong"
BASE = "https://www.stayforlong.de/de-de/hotel/th/santiburi-koh-samui_koh-samui"

# "[Streichpreis €\n]Gesamtpreis €\nPreis/Nacht €\n/ Nacht\n(13 Naechte)" - der
# Gesamtpreis gilt fuer ALLE Zimmer der Suche, "/ Nacht" ist Gesamt / Naechte.
_RATE = re.compile(
    r"(?:([\d.]+)\s?€\n)?([\d.]+)\s?€\n([\d.]+)\s?€\n/ Nacht\n\((\d+) N",
)


def _url(checkin: date, checkout: date, shape: list[int]) -> str:
    params = {"checkIn": checkin.isoformat(), "checkOut": checkout.isoformat(),
              "adults": ",".join(str(n) for n in shape),
              "children": "!" * (len(shape) - 1)}
    return BASE + "?" + urlencode(params, safe=",")


def parse_rates(text: str) -> list[dict]:
    rows: list[dict] = []
    prev_end = 0
    for m in _RATE.finditer(text):
        total = parse_money(m.group(2))
        per_night_all = parse_money(m.group(3))
        nights = int(m.group(4))
        window = text[max(prev_end, m.start() - 420):m.start()]
        prev_end = m.end()
        if not total or not nights:
            continue
        if per_night_all and abs(total / nights - per_night_all) > 2:
            continue  # Zahlen passen nicht zusammen -> kein Angebot, sondern Fehlparse
        rows.append({
            "total": total, "nights": nights,
            "refundable": "KOSTENLOSE Stornierung" in window,
            "breakfast": "Frühstück inklusive" in window,
            "non_refundable": "Nicht erstattbar" in window,
            "deposit": "Anzahlung" in window,
        })
    return rows


async def _search(cfg: Config, url: str) -> list[dict]:
    async with browser_page(cfg) as page:
        await goto(page, url)
        with contextlib.suppress(Exception):
            await dismiss_consent(page)
        with contextlib.suppress(Exception):
            await page.wait_for_function("document.body.innerText.includes('/ Nacht')",
                                         timeout=30000)
        await page.wait_for_timeout(2000)
        body = ""
        with contextlib.suppress(Exception):
            body = await page.inner_text("body", timeout=8000)
        return parse_rates(body)


async def fetch(cfg: Config) -> HotelOffer:
    t = cfg.trip
    checkin, checkout = t.hotel_checkin, t.hotel_checkout
    nights = (checkout - checkin).days
    allocations = (explicit_allocations(t.hotel.allowed_room_shapes, t.persons)
                   if t.hotel.allowed_room_shapes
                   else candidate_allocations(t.persons, t.max_persons_per_room))
    offer = HotelOffer(source=SOURCE, ok=False, price_total=None, currency=t.currency,
                       nights=nights, rooms=t.rooms, guests=t.persons)
    best: tuple[dict, dict, str, list[int]] | None = None   # (row, alloc, url, shape)
    best_refundable: float | None = None
    per_shape: dict[str, dict] = {}
    for alloc in allocations:
        shape = sorted((size for size, cnt in alloc.items() for _ in range(cnt)), reverse=True)
        url = _url(checkin, checkout, shape)
        try:
            rows = await _search(cfg, url)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s: Aufteilung %s fehlgeschlagen: %s", SOURCE, shape, exc)
            per_shape["+".join(map(str, shape))] = {"url": url, "error": f"{type(exc).__name__}: {exc}"}
            continue
        rows = [r for r in rows if r["nights"] == nights] or rows
        if not rows:
            per_shape["+".join(map(str, shape))] = {"url": url, "offers": 0}
            log.info("%s: Aufteilung %s -> keine Preise lesbar", SOURCE, shape)
            continue
        cheapest = min(rows, key=lambda r: r["total"])
        refund = [r["total"] for r in rows if r["refundable"]]
        per_shape["+".join(map(str, shape))] = {
            "url": url, "offers": len(rows), "cheapest": cheapest["total"],
            "cheapest_refundable": min(refund) if refund else None}
        log.info("%s: Aufteilung %s -> %d Raten, ab %.0f %s", SOURCE, shape, len(rows),
                 cheapest["total"], t.currency)
        if best is None or cheapest["total"] < best[0]["total"]:
            best = (cheapest, alloc, url, shape)
        if refund and (best_refundable is None or min(refund) < best_refundable):
            best_refundable = min(refund)

    offer.raw = {"checkin": checkin.isoformat(), "checkout": checkout.isoformat(),
                 "per_shape": per_shape, "all_in": True,
                 "basis": "Gesamtpreis fuer alle Zimmer in EINER Suche (Zimmeraufteilung per URL), "
                          "inkl. Steuern und Gebuehren"}
    if not best:
        offer.error = "keine Preise lesbar (Seite/Layout geaendert oder blockiert?)"
        log.warning("%s: %s (%s)", SOURCE, offer.error, per_shape)
        return offer
    row, alloc, url, shape = best
    offer.ok = True
    offer.price_total = round(row["total"], 2)
    offer.per_night = round(row["total"] / nights, 2) if nights else None
    offer.rooms = len(shape)
    offer.deep_link = url
    tags = [x for x, on in (("Frühstück inkl.", row["breakfast"]),
                            ("nicht erstattbar", row["non_refundable"]),
                            ("mit Anzahlung", row["deposit"]),
                            ("kostenlos stornierbar", row["refundable"])) if on]
    offer.raw.update({
        "room_split": {str(k): v for k, v in alloc.items()},
        "per_room_size": {str(size): {"url": url, "cheapest": None} for size in alloc},
        "rate_note": " · ".join(tags) + (" · Aktionspreis, kann sich ändern" if tags else "Aktionspreis, kann sich ändern"),
        "cheapest_refundable": best_refundable,
    })
    log.info("%s: gesamt %.0f %s (%d Naechte, Aufteilung %s)", SOURCE, offer.price_total,
             t.currency, nights, shape)
    return offer
