"""Santiburi Koh Samui - Preis DIREKT von der offiziellen Buchungsmaschine.

Kein Rate-Parity-Umweg ueber OTAs: santiburisamui.com verlinkt "BOOK NOW" auf
die eigene SynXis-Buchungsmaschine (reservation.santiburisamui.com) mit
Hotel-/Chain-ID direkt in der URL - Termin, Waehrung und Belegung lassen sich
komplett per Query-String steuern, kein Formular noetig:

    https://reservation.santiburisamui.com/?chain=34270&hotel=95417
        &adult=3&child=0&rooms=1&arrive=2027-05-15&depart=2027-05-28
        &currency=EUR&productcurrency=EUR&level=hotel&locale=en-US

Wie bei CHECK24 wird deshalb NICHT "3 Zimmer/8 Personen" auf einmal gesucht,
sondern **je Zimmergroesse einzeln** (``room_split``) und die guenstigsten
Treffer je Groesse x Anzahl Zimmer dieser Groesse aufsummiert.

Die Seite ist eine JS-SPA (Preise kommen per XHR nach dem Laden) - Playwright
noetig, kein einfacher Server-Fetch wie bei Google Hotels.
"""
from __future__ import annotations

import contextlib
import re
from datetime import date
from urllib.parse import urlencode

from ...config import Config
from ...logging_setup import get_logger
from ...offers import HotelOffer
from ..room_split import candidate_allocations, cheapest_allocation, explicit_allocations
from ..scraper_base import browser_page, dismiss_consent, goto, parse_money, save_screenshot

log = get_logger("source.santiburi")
SOURCE = "santiburi_official"

BASE = "https://reservation.santiburisamui.com/"
_PRICE_BLOCK = re.compile(
    r"€\s?([\d.,]+)\nPer Night\n€\s?([\d.,]+)\s*Total for (\d+) nights?",
)
# Nach jeder Ratenkarte folgt eine GROSSBUCHSTABEN-Zeile mit Zimmer+Raten-Name,
# z.B. "DUPLEX SUITE DISCOVERY | BEST FLEXIBLE RATE".
_LABEL_AFTER = re.compile(r"(?<=\n)([A-Z][A-Z0-9 &|/'-]{4,70})(?=\n)")
_REFUNDABLE_HINT = re.compile(r"Free cancellation", re.I)
_NONREFUNDABLE_HINT = re.compile(r"Deposit Required|Non-?refundable|Guaranteed with Credit Card", re.I)


def _url(chain: str, hotel_id: str, checkin: date, checkout: date, adults: int) -> str:
    params = {
        "chain": chain, "hotel": hotel_id, "adult": adults, "child": 0, "rooms": 1,
        "arrive": checkin.isoformat(), "depart": checkout.isoformat(),
        "currency": "EUR", "productcurrency": "EUR", "level": "hotel", "locale": "en-US",
    }
    return BASE + "?" + urlencode(params)


def _refundable_near(text: str, pos: int) -> bool | None:
    window = text[max(0, pos - 400):pos]
    if _REFUNDABLE_HINT.search(window):
        return True
    if _NONREFUNDABLE_HINT.search(window):
        return False
    return None


def _parse_rows(text: str, room_hint: str = "") -> list[dict]:
    out = []
    for m in _PRICE_BLOCK.finditer(text):
        total = parse_money(m.group(2))
        if not total:
            continue
        label = ""
        for lm in _LABEL_AFTER.finditer(text, m.end(), m.end() + 250):
            cand = lm.group(1).strip()
            if cand not in ("BOOK NOW", "SELECT A ROOM", "VIEW MORE RATES"):
                label = cand
                break
        out.append({
            "room": label,
            "per_night": parse_money(m.group(1)),
            "total": total,
            "nights": int(m.group(3)),
            "refundable": _refundable_near(text, m.start()),
        })
    return _filter_room_category(out, room_hint)


def _filter_room_category(rows: list[dict], room_hint: str) -> list[dict]:
    """Gleiche Ursache wie der CHECK24-Bug (13.09.26, siehe
    hotels/check24.py): die Buchungsseite listet ALLE Zimmerkategorien
    (Standard bis Villa) auf einer Suchseite, nicht nur die konfigurierte
    (``HotelCfg.room_type_hint``) - ungefiltert gewinnt das billigste
    Zimmer ueber alle Kategorien hinweg. Faellt auf ungefiltert zurueck,
    falls kein Zimmername den Hint enthaelt."""
    if not room_hint:
        return rows
    hint = room_hint.lower()
    matching = [r for r in rows if hint in r["room"].lower()]
    if not matching:
        log.warning("%s: keine Zimmerkategorie enthaelt Hinweis '%s' - "
                   "ungefiltert weiterverwendet (%d Angebote, Kategorien: %s)",
                   SOURCE, room_hint, len(rows),
                   sorted({r["room"] for r in rows if r["room"]})[:8])
        return rows
    return matching


async def _search_one(cfg: Config, url: str, room_hint: str = "") -> list[dict]:
    async with browser_page(cfg) as page:
        await goto(page, url)
        await page.wait_for_timeout(1000)
        with contextlib.suppress(Exception):
            await page.keyboard.press("Escape")  # Promo-/Consent-Layer schliessen
        await dismiss_consent(page)
        with contextlib.suppress(Exception):
            await page.wait_for_function(
                "document.body.innerText.includes('Total for') || "
                "document.body.innerText.includes('No rooms available') || "
                "document.body.innerText.includes('Sorry')",
                timeout=20000,
            )
        await page.wait_for_timeout(1000)
        body = ""
        with contextlib.suppress(Exception):
            body = await page.inner_text("body", timeout=6000)
        rows = _parse_rows(body, room_hint)
        if not rows and cfg.sources.scraper.screenshot_on_error:
            await save_screenshot(page, SOURCE)
        return rows


async def fetch(cfg: Config) -> HotelOffer:
    t = cfg.trip
    checkin, checkout = t.hotel_checkin, t.hotel_checkout
    nights = (checkout - checkin).days
    allocations = (explicit_allocations(t.hotel.allowed_room_shapes, t.persons)
                  if t.hotel.allowed_room_shapes
                  else candidate_allocations(t.persons, t.max_persons_per_room))

    offer = HotelOffer(source=SOURCE, ok=False, price_total=None,
                       currency=t.currency, nights=nights, rooms=t.rooms,
                       guests=t.persons, room_desc=t.hotel.room_type_hint)

    chain_id, hotel_id = "34270", "95417"  # Santiburi Koh Samui (SynXis IBE)
    if not allocations:
        offer.error = "keine gueltige Zimmeraufteilung (trip.max_persons_per_room pruefen)"
        return offer

    per_size: dict[str, dict] = {}
    sizes = sorted({size for alloc in allocations for size in alloc})
    for size in sizes:
        url = _url(chain_id, hotel_id, checkin, checkout, size)
        try:
            rows = await _search_one(cfg, url, t.hotel.room_type_hint)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s: 1 Zimmer/%d Erw. fehlgeschlagen: %s", SOURCE, size, exc)
            per_size[str(size)] = {"url": url, "error": f"{type(exc).__name__}: {exc}"}
            continue
        if not rows:
            log.info("%s: 1 Zimmer/%d Erw. -> keine Preise lesbar", SOURCE, size)
            per_size[str(size)] = {"url": url, "offers": 0}
        else:
            cheapest_row = min(rows, key=lambda r: r["total"])
            refund = [r["total"] for r in rows if r["refundable"]]
            log.info("%s: 1 Zimmer/%d Erw. -> %d Raten, ab %.0f %s",
                     SOURCE, size, len(rows), cheapest_row["total"], t.currency)
            per_size[str(size)] = {
                "url": url, "offers": len(rows), "cheapest": cheapest_row["total"],
                "cheapest_room": cheapest_row["room"] or None,
                "cheapest_refundable": min(refund) if refund else None,
            }

    price_per_size = {size: info["cheapest"] for size, info in
                      ((s, per_size.get(str(s), {})) for s in sizes)
                      if info.get("cheapest") is not None}
    refund_per_size = {size: info["cheapest_refundable"] for size, info in
                       ((s, per_size.get(str(s), {})) for s in sizes)
                       if info.get("cheapest_refundable") is not None}
    best = cheapest_allocation(allocations, price_per_size)
    best_refund = cheapest_allocation(allocations, refund_per_size)

    offer.raw = {
        "candidate_allocations": [{str(k): v for k, v in a.items()} for a in allocations],
        "per_room_size": per_size,
        "basis": ("Live-Summe direkt vom Hotel: je Zimmergroesse einzeln gesucht "
                  "(1 Zimmer, N Erwachsene), guenstigste Kombination aus allen "
                  "sinnvollen Zimmer-Aufteilungen gewaehlt - keine OTA-Marge"),
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
        offer.raw["refundable_total"] = round(best_refund[1], 2) if best_refund else None
        log.info("%s: gesamt %.0f %s (%d Naechte, guenstigste Aufteilung %s von %d geprueften)",
                 SOURCE, offer.price_total, t.currency, nights, counts, len(allocations))
    else:
        offer.error = "nicht fuer alle Zimmergroessen ein Preis gefunden"
        log.warning("%s: %s (%s)", SOURCE, offer.error, per_size)
    return offer
