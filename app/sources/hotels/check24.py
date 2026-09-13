"""Hotelpreis via CHECK24 - der GENAUE Live-Preis (nutzernachempfunden).

CHECK24 rechnet "3 Zimmer / 8 Personen" in der Suchmaske oft schlecht
(Aufpreise, seltsame Zimmerzuteilung). Der zuverlaessige Weg, den der Nutzer
manuell nutzt: **jede Zimmergroesse einzeln suchen** (1 Zimmer, N Erwachsene)
und die guenstigsten Treffer je Groesse x Anzahl Zimmer dieser Groesse
aufsummieren. Bei 8 Personen / 3 Zimmern (max. 3 Pers./Zimmer) ergibt das
automatisch die vom Nutzer beschriebene Aufteilung 2x "1 Zimmer/3 Erwachsene"
+ 1x "1 Zimmer/2 Erwachsene".

Technischer Trick: CHECK24 kodiert Ziel, Termin und Belegung direkt in der
URL - keine Formulareingabe noetig:

    https://hotel.check24.de/search/<HotelName>-<HotelId>/<checkin>/<checkout>/[A|A|A]/hotel.html

``[A|A|A]`` = ein Zimmer mit 3 Erwachsenen (A = adult, "|"-getrennt).
``<HotelId>`` einmalig per Suche auf hotel.check24.de ermitteln (siehe
``HotelCfg.check24_slug_id`` in config.yaml).

Bewusst LANGSAM getaktet (20-45s Pause zwischen den Teil-Suchen): der Check
laeuft ohnehin nur alle paar Stunden, Zeitdruck besteht nicht - das schont
CHECK24 und senkt das Risiko einer Bot-Erkennung.
"""
from __future__ import annotations

import asyncio
import contextlib
import random
import re
from datetime import date
from urllib.parse import quote

from ...config import Config
from ...logging_setup import get_logger
from ...offers import HotelOffer
from ..room_split import candidate_allocations, cheapest_allocation
from ..scraper_base import browser_page, dismiss_consent, goto, parse_money, save_screenshot

log = get_logger("source.check24")
SOURCE = "check24"

BASE = "https://hotel.check24.de"
_ROW = re.compile(
    r"([\d][\d.,]*)\s*€\s*\n[\d.,]+\s*€ als Smily\s*\nPunkte sammeln\n(.*?)"
    r"Details zum Angebot von ([^\n]{1,40})\n(?:Zimmer:\s*([^\n]{1,80}))?",
    re.S,
)
# CHECK24 markiert EINE Karte oben auf der Seite explizit als "die
# guenstigste Option zu Ihrer Suche" - live-verifiziert 14.09.26: fuer 1
# Zimmer/3 Erwachsene zeigte diese Karte 3.610EUR, die lange Liste
# darunter ("Alle verfuegbaren Zimmer", nach Beliebtheit sortiert, NICHT
# nach Preis) enthielt aber zusaetzlich mehrere Zeilen um 2.300-2.450EUR,
# die CHECK24 selbst NICHT als die guenstigste Option fuehrt (vermutlich
# "Vergleichbare Angebote"/veraltete Cache-Preise, nicht zuverlaessig
# buchbar). _drop_price_outliers() (Median-basiert) greift hier NICHT,
# weil diese Zeilen sich untereinander kaum unterscheiden - nur CHECK24s
# eigene Badge verrrraet, dass sie trotzdem nicht die echte Untergrenze sind.
_CHEAPEST_BADGE = re.compile(
    r"Günstigster Preis\s*\nDieses Angebot ist die günstigste Option zu Ihrer Suche\.\s*\n"
    r"(?P<room>[^\n]{1,60})\s*\n"
    r".*?Angebot von (?P<supplier>[^\n]{1,40})\s*\n"
    r"Preis für alle Reisenden\s*\n[^\n]*\n"
    r"(?:[\d.,]+\s*€\s*\n)?"
    r"(?P<price>[\d.,]+)\s*€\s*\n\s*buchen",
    re.S,
)


def _occupancy_param(adults: int) -> str:
    return "[" + "|".join(["A"] * adults) + "]"


def _url(slug_id: str, checkin: date, checkout: date, adults: int) -> str:
    path = f"/search/{quote(slug_id)}/{checkin.isoformat()}/{checkout.isoformat()}/{quote(_occupancy_param(adults))}/hotel.html"
    return BASE + path


def _refundable(block: str) -> bool | None:
    low = block.lower()
    if "nicht kostenlos stornierbar" in low:
        return False
    if "kostenlos stornierbar" in low:
        return True
    return None


def _parse_rows(text: str, room_hint: str = "") -> list[dict]:
    out = []
    for m in _ROW.finditer(text):
        price = parse_money(m.group(1))
        if not price:
            continue
        out.append({
            "price": price,
            "refundable": _refundable(m.group(2)),
            "supplier": m.group(3).strip(),
            "room": (m.group(4) or "").strip(),
        })
    out = _filter_room_category(out, room_hint)
    out = _drop_price_outliers(out)
    return _apply_cheapest_badge(out, text)


def _apply_cheapest_badge(rows: list[dict], text: str) -> list[dict]:
    """Verwirft Zeilen, deren Preis UNTER CHECK24s eigener "Guenstigster
    Preis"-Badge liegt (siehe _CHEAPEST_BADGE-Kommentar) - die sind laut
    CHECK24 selbst nicht die tatsaechlich guenstigste buchbare Option, ein
    blindes min() darueber waere zu niedrig. Bleibt die Badge-Karte selbst
    (noch) nicht in `rows` (z.B. weil ihr Format nicht zu _ROW passt), wird
    sie als eigene Zeile ergaenzt."""
    m = _CHEAPEST_BADGE.search(text)
    if not m:
        return rows
    badge_price = parse_money(m.group("price"))
    if not badge_price:
        return rows
    kept = [r for r in rows if r["price"] >= badge_price - 1]
    if not any(abs(r["price"] - badge_price) < 1 for r in kept):
        kept.append({
            "price": badge_price, "refundable": None,
            "supplier": m.group("supplier").strip(), "room": m.group("room").strip(),
        })
    dropped = len(rows) - len([r for r in rows if r["price"] >= badge_price - 1])
    if dropped:
        log.warning("%s: %d Angebot(e) unter CHECK24s eigener 'Guenstigster Preis'-"
                   "Badge (%.0f) verworfen - nicht die tatsaechlich guenstigste Option",
                   SOURCE, dropped, badge_price)
    return kept


def _filter_room_category(rows: list[dict], room_hint: str) -> list[dict]:
    """CHECK24 listet auf einer Suchseite ALLE Zimmerkategorien des Hotels
    (Standardzimmer bis Villa) - nicht nur die vom Nutzer gewuenschte
    (``HotelCfg.room_type_hint``, z.B. "Grand Reserve Villa"). Ohne diesen
    Filter nimmt der Code das billigste Angebot ueber ALLE Kategorien hinweg
    (z.B. ein 551EUR-Standardzimmer statt der gebuchten Villa) - das erklaert
    die massive Diskrepanz zu google_hotels/Referenz (Live-Fund 13.09.26:
    check24 zeigte 1.653EUR statt ~7-8.000EUR fuer 3 Villen). Faellt auf
    "keine Filterung" zurueck, falls kein Zimmername den Hint enthaelt -
    besser ein zu hoher als ein falsch niedriger Preis."""
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


def _drop_price_outliers(rows: list[dict]) -> list[dict]:
    """Verwirft Zeilen, deren Preis absurd weit unter dem Median aller
    gefundenen Angebote liegt (Live-Fund 13.09.26: Tapstay zeigte fuer "1
    Zimmer/2 Erwachsene" 551EUR unter der Zimmerbezeichnung "Deluxe Villa,
    Beachfront", obwohl andere Anbieter fuer DASSELBE Zimmer 8.500-10.600EUR
    zeigten - offenbar ein fehlgeleiteter/irrefuehrender Anker-Preis, keine
    echte Buchbarkeit fuer dieses Zimmer. Der alte Code nahm blind das
    globale Minimum aller Angebote -> das haette den Hotelpreis um Faktor
    ~4 zu niedrig ausgewiesen (1.653EUR statt ~7.000EUR fuer 3 Villen).
    Schwelle: unter 40% des Median gilt als unplausibel und wird verworfen,
    NICHT einfach der 2.-guenstigste genommen - falls ALLE Zeilen so ein
    Ausreisser waeren, bleibt die Liste leer und die Suche zaehlt als
    "kein Preis lesbar" statt einen falschen Wert zu liefern."""
    if len(rows) < 3:
        return rows
    prices = sorted(r["price"] for r in rows)
    mid = len(prices) // 2
    median = prices[mid] if len(prices) % 2 else (prices[mid - 1] + prices[mid]) / 2
    threshold = median * 0.4
    kept = [r for r in rows if r["price"] >= threshold]
    dropped = len(rows) - len(kept)
    if dropped:
        log.warning("%s: %d von %d Angeboten als Preis-Ausreisser verworfen "
                   "(< 40%% des Median %.0f) - z.B. %s",
                   SOURCE, dropped, len(rows), median,
                   [r["price"] for r in rows if r["price"] < threshold][:5])
    return kept or rows  # nie eine leere Liste zurueckgeben, wenn ALLE "Ausreisser" waeren


async def _search_one(cfg: Config, url: str, room_hint: str = "") -> list[dict]:
    async with browser_page(cfg) as page:
        await goto(page, url)
        await page.wait_for_timeout(1200)
        await dismiss_consent(page)
        await page.wait_for_timeout(800)
        # "Zimmer & Preise"-Tab oeffnen, dort steht die Preistabelle
        with contextlib.suppress(Exception):
            tab = page.get_by_text("Zimmer & Preise", exact=False).first
            if await tab.count():
                await tab.click(timeout=4000)
        # CHECK24 sucht live bei mehreren Anbietern - das dauert; auf das
        # tatsaechliche Erscheinen der Preiszeilen warten statt fest zu pausieren.
        with contextlib.suppress(Exception):
            await page.wait_for_function(
                "document.body.innerText.includes('Details zum Angebot von') || "
                "document.body.innerText.includes('Keine verfügbaren Zimmer') || "
                "document.body.innerText.includes('Leider sind für Ihre Suche')",
                timeout=25000,
            )
        await page.wait_for_timeout(1500)
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=6000)
        body = ""
        with contextlib.suppress(Exception):
            body = await page.inner_text("body", timeout=6000)
        rows = _parse_rows(body, room_hint)
        if not rows and cfg.sources.scraper.screenshot_on_error:
            await save_screenshot(page, SOURCE)
        return rows


async def fetch(cfg: Config) -> HotelOffer:
    t = cfg.trip
    slug_id = t.hotel.check24_slug_id
    checkin, checkout = t.hotel_checkin, t.hotel_checkout
    nights = (checkout - checkin).days
    # Nicht mehr EINE feste Aufteilung suchen, sondern alle sinnvollen
    # Kandidaten (13.09.26 Nutzervorgabe: 2x3+1x2 ist nicht automatisch am
    # guenstigsten) - dafuer reicht es, jede vorkommende Zimmergroesse EINMAL
    # zu suchen; welche Kombination am Ende gewinnt, wird erst nach dem
    # Sammeln aller Preise entschieden (siehe cheapest_allocation unten).
    allocations = candidate_allocations(t.persons, t.max_persons_per_room)

    offer = HotelOffer(source=SOURCE, ok=False, price_total=None,
                       currency=t.currency, nights=nights, rooms=t.rooms,
                       guests=t.persons, room_desc=t.hotel.room_type_hint)

    if not slug_id:
        offer.error = ("hotel.check24_slug_id nicht gesetzt - einmalig auf "
                       "hotel.check24.de suchen und die '<Name>-<ID>' aus der "
                       "URL in config.yaml eintragen")
        log.warning("%s: %s", SOURCE, offer.error)
        return offer
    if not allocations:
        offer.error = "keine gueltige Zimmeraufteilung (trip.max_persons_per_room pruefen)"
        return offer

    lo, hi = cfg.sources.hotels.check24_delay_seconds
    per_size: dict[str, dict] = {}
    sizes = sorted({size for alloc in allocations for size in alloc})
    for i, size in enumerate(sizes):
        url = _url(slug_id, checkin, checkout, size)
        try:
            rows = await _search_one(cfg, url, t.hotel.room_type_hint)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s: 1 Zimmer/%d Erw. fehlgeschlagen: %s", SOURCE, size, exc)
            per_size[str(size)] = {"url": url, "error": f"{type(exc).__name__}: {exc}"}
            continue
        if not rows:
            log.info("%s: 1 Zimmer/%d Erw. -> keine Angebote lesbar", SOURCE, size)
            per_size[str(size)] = {"url": url, "offers": 0}
        else:
            cheapest = min(r["price"] for r in rows)
            refund_prices = [r["price"] for r in rows if r["refundable"]]
            log.info("%s: 1 Zimmer/%d Erw. -> %d Angebote, ab %.0f %s",
                     SOURCE, size, len(rows), cheapest, t.currency)
            per_size[str(size)] = {
                "url": url, "offers": len(rows), "cheapest": cheapest,
                "cheapest_refundable": min(refund_prices) if refund_prices else None,
            }
        if i < len(sizes) - 1:
            await asyncio.sleep(random.uniform(lo, hi))

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
        "basis": ("Live-Summe: je Zimmergroesse einzeln gesucht (1 Zimmer, N "
                  "Erwachsene), guenstigste Kombination aus allen sinnvollen "
                  "Zimmer-Aufteilungen gewaehlt (nicht mehr fest 2x3+1x2)"),
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
        offer.error = "fuer keine Zimmeraufteilung durchgehend ein Preis gefunden"
        log.warning("%s: %s (%s)", SOURCE, offer.error, per_size)
    return offer
