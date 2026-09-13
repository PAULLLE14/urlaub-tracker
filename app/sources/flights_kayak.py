"""Kayak als zweite, unabhaengige Flugquelle (zusaetzlich zu Google Flights).

Nutzervorgabe 14.09.26: "wir brauchen so viele Angebotsseiten wie moeglich" -
eine zweite Quelle deckt Faelle ab, in denen Google Flights/fast-flights eine
guenstige Verbindung schlicht nicht zeigt (andere OTA-Vertraege, anderes
Caching). Wie bei allen anderen Quellen: Playwright (kein Formular, direkter
Such-Link), 1-Pax-Suche x Personenzahl hochgerechnet (``pax_mode=
"estimated"``), fliesst in denselben Vergleich/Dedup/Gruppen-Check wie die
Google-Flights-Angebote ein.

    https://www.kayak.de/flights/FRA-USM/2027-05-14/2027-05-28/1adults?sort=price_a

WICHTIG (``single_ticket_only``-Aequivalent): Kayak mischt in derselben
Ergebnisliste "durchgehende" Verbindungen und "Eigenstaendiger Transfer"
(= getrennte Tickets, kein Umbuchungsschutz bei Verspaetung) - Karten mit
diesem Tag werden hier hart aussortiert, exakt dieselbe Regel wie bei Google
Flights' ``hide_separate_and_self_transfer``.

Datenqualitaet: Kayak zeigt (anders als Google Flights bei Round-Trip) BEIDE
Strecken mit echten Zeiten/Stopps/Layover-Flughaefen in einer Karte - keine
Extra-Suche noetig. Die einzelnen Segment-ZWISCHENzeiten (bei >1 Stopp)
sieht man trotzdem nicht, nur Gesamt-Abflug/-Ankunft/-Dauer je Richtung -
deshalb wie bei den anderen Playwright-Quellen ``segment_times_approximate
= True`` (siehe flight_cards.build_approx_segments).
"""
from __future__ import annotations

import re
from datetime import date

from ..config import Config
from ..logging_setup import get_logger
from ..offers import FlightOffer
from .flight_cards import build_approx_segments
from .scraper_base import (
    browser_page,
    dismiss_consent,
    expand_more_results,
    goto,
    parse_money,
    save_screenshot,
    wait_for_stable_result_count,
)

log = get_logger("source.flights_kayak")
SOURCE = "kayak"

BASE = "https://www.kayak.de/flights"
_AIRPORT_CODE = re.compile(r"\b[A-Z]{3}\b")

# Eine Ergebniskarte (Round-Trip, beide Strecken in einer Karte) - siehe
# Modul-Docstring fuer ein reales Beispiel. Non-greedy Layover-Bloecke
# stoppen am jeweils naechsten "X Std." (Gesamtdauer der Strecke).
_CARD = re.compile(
    r"Zu den Ergebnisdetails\n"
    r"(?P<selftransfer>Eigenständiger Transfer\n)?"
    r"(?P<out_dep>\d{1,2}:\d{2})\s*–\s*(?P<out_arr>\d{1,2}:\d{2})(?:\+\d+)?\n"
    r"(?P<out_origin>[A-Z]{3})[^\n]*\n-\n(?P<out_dest>[A-Z]{3})[^\n]*\n"
    r"(?P<out_stops>Nonstop|\d+\s*Stopps?)\n"
    r"(?P<out_layover>.*?)"
    r"(?P<out_duration>\d+:\d+)\s*Std\.\n"
    r"(?P<ret_dep>\d{1,2}:\d{2})\s*–\s*(?P<ret_arr>\d{1,2}:\d{2})(?:\+\d+)?\n"
    r"(?P<ret_origin>[A-Z]{3})[^\n]*\n-\n(?P<ret_dest>[A-Z]{3})[^\n]*\n"
    r"(?P<ret_stops>Nonstop|\d+\s*Stopps?)\n"
    r"(?P<ret_layover>.*?)"
    r"(?P<ret_duration>\d+:\d+)\s*Std\.\n"
    r"(?P<airlines>[^\n]+)\n"
    r"\d+\n\d+\n"
    r"(?P<price>[\d.,]+)\s*€",
    re.S,
)


def _url(origin: str, dest: str, out_d: date, ret_d: date, adults: int) -> str:
    return (f"{BASE}/{origin}-{dest}/{out_d.isoformat()}/{ret_d.isoformat()}/"
            f"{adults}adults?sort=price_a")


def _stops_count(text: str) -> int:
    if text.strip() == "Nonstop":
        return 0
    m = re.search(r"(\d+)", text)
    return int(m.group(1)) if m else 0


def _duration_minutes(text: str) -> int:
    h, m = text.split(":")
    return int(h) * 60 + int(m)


def _parse_cards(body: str) -> list[dict]:
    out = []
    for m in _CARD.finditer(body):
        price = parse_money(m.group("price"))
        if not price:
            continue
        if m.group("selftransfer") or "Eigenständiger Transfer" in m.group("out_layover") \
                or "Eigenständiger Transfer" in m.group("ret_layover"):
            # single_ticket_only-Aequivalent: getrennte Tickets ohne
            # gemeinsamen Umbuchungsschutz werden nicht angezeigt.
            continue
        out_stops = _stops_count(m.group("out_stops"))
        ret_stops = _stops_count(m.group("ret_stops"))
        out_layovers = _AIRPORT_CODE.findall(m.group("out_layover"))
        ret_layovers = _AIRPORT_CODE.findall(m.group("ret_layover"))
        if len(out_layovers) != out_stops or len(ret_layovers) != ret_stops:
            # Karte nicht sauber geparst - lieber ueberspringen als falsche
            # Airports uebernehmen (gleiche Regel wie flight_cards.py).
            continue
        out.append({
            "price": price, "airlines": [a.strip() for a in m.group("airlines").split(",")],
            "out_dep": m.group("out_dep"), "out_origin": m.group("out_origin"),
            "out_dest": m.group("out_dest"), "out_layovers": out_layovers,
            "out_duration_min": _duration_minutes(m.group("out_duration")),
            "ret_dep": m.group("ret_dep"), "ret_origin": m.group("ret_origin"),
            "ret_dest": m.group("ret_dest"), "ret_layovers": ret_layovers,
            "ret_duration_min": _duration_minutes(m.group("ret_duration")),
        })
    return out


async def _search_one(cfg: Config, origin: str, out_d: date, ret_d: date) -> list[FlightOffer]:
    t = cfg.trip
    url = _url(origin, t.destination_airport, out_d, ret_d, 1)
    async with browser_page(cfg) as page:
        await goto(page, url)
        await dismiss_consent(page)
        import contextlib
        with contextlib.suppress(Exception):
            await page.wait_for_function(
                "document.body.innerText.includes('Ergebnisse') || "
                "document.body.innerText.includes('Keine Ergebnisse')",
                timeout=30000,
            )
        await wait_for_stable_result_count(page, "Zu den Ergebnisdetails", max_wait=20.0)
        await expand_more_results(page)
        body = ""
        with contextlib.suppress(Exception):
            body = await page.inner_text("body", timeout=6000)
        cards = _parse_cards(body)
        if not cards:
            if cfg.sources.scraper.screenshot_on_error:
                await save_screenshot(page, SOURCE)
            return []

    persons = t.persons
    offers = []
    for c in cards:
        out_segs = build_approx_segments(c["out_origin"], c["out_dest"], c["out_layovers"],
                                         c["out_dep"], c["out_duration_min"], out_d)
        ret_segs = build_approx_segments(c["ret_origin"], c["ret_dest"], c["ret_layovers"],
                                         c["ret_dep"], c["ret_duration_min"], ret_d)
        offers.append(FlightOffer(
            source=SOURCE, direction="round_trip", trip_type="round_trip",
            origin=origin, destination=t.destination_airport,
            search_date=out_d, return_date=ret_d,
            price_total=round(c["price"] * persons, 2), price_per_person=round(c["price"], 2),
            currency=t.currency, airlines=c["airlines"], segments=out_segs,
            return_segments=ret_segs, deep_link=url, pax_mode="estimated",
            segment_times_approximate=True,
        ))
    return offers


async def fetch(cfg: Config) -> tuple[list[FlightOffer], dict]:
    t, fc = cfg.trip, cfg.sources.flights_kayak
    if not fc.enabled:
        return [], {"ok": True, "count": 0, "attempted": 0, "error": "deaktiviert"}
    import asyncio
    import random

    pairs = t.rt_date_pairs()
    lo, hi = fc.request_delay_seconds
    offers: list[FlightOffer] = []
    attempted = failed = 0
    first_error = ""
    for i, origin in enumerate(t.origin_airports):
        for out_d, ret_d in pairs:
            attempted += 1
            label = f"Kayak {origin} {out_d}/{ret_d}"
            try:
                got = await _search_one(cfg, origin, out_d, ret_d)
                offers.extend(got)
                log.info("%s: %d Angebote", label, len(got))
            except Exception as exc:  # noqa: BLE001
                failed += 1
                first_error = first_error or f"{label}: {type(exc).__name__}: {exc}"
                log.warning("%s: Fehler %s: %s", label, type(exc).__name__, exc)
            if i < len(t.origin_airports) - 1:
                await asyncio.sleep(random.uniform(lo, hi))
    return offers, {
        "ok": failed == 0, "count": len(offers), "attempted": attempted,
        "failed": failed, "error": first_error,
    }


def collect(cfg: Config) -> tuple[list[FlightOffer], dict]:
    """Synchroner Wrapper (gleiches Muster wie die anderen Playwright-Quellen)."""
    from .scraper_base import playwright_available

    if not cfg.sources.flights_kayak.enabled:
        return [], {"ok": True, "count": 0, "attempted": 0, "error": "deaktiviert"}
    ok, err = playwright_available()
    if not ok:
        return [], {"ok": False, "count": 0, "attempted": 0, "error": err}
    import asyncio

    try:
        return asyncio.run(fetch(cfg))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(fetch(cfg))
        finally:
            loop.close()
    except Exception as exc:  # noqa: BLE001
        return [], {"ok": False, "count": 0, "attempted": 0,
                    "error": f"{type(exc).__name__}: {exc}"}
