"""Multi-City-Flugsuche via echtem Playwright-Browser (NICHT primp/fast-flights).

Hintergrund (verifiziert 12.09.26): ``sources/flights.py`` nutzt fuer alle
Suchen primp (reiner HTTP-Request, kein JS). Fuer Multi-City-Routen zu USM
liefert Google dabei reproduzierbar KEIN Ergebnis (``payload[3]`` ist ``None``
oder leer - 3/3 identische leere Antworten in einem Diagnose-Skript, auch mit
Pausen dazwischen). Ein echter Browser (Playwright, wie bei den Hotel-
Quellen) bekommt dieselbe Route dagegen zuverlaessig innerhalb weniger
Sekunden angezeigt - Google laedt die Multi-City-Ergebnisse offenbar per
Nachlade-Request, den ein reiner HTTP-Fetch nie ausloest. Deshalb hier ein
zweiter, browserbasierter Weg NUR fuer Multi-City.

Ablauf pro Route (zwei echte Seiten-Interaktionen, daher spuerbar teurer als
der normale primp-Weg - nur fuer die primaere Datumspaarung genutzt):
  1. Hinflug-Ergebnisliste laden, alle Karten parsen, guenstigste waehlen.
  2. Diese Karte anklicken (Google zeigt danach die zum Hinflug passende
     Rueckflug-Ergebnisliste - eine eigene "Wohin als naechstes"-Seite ohne
     eigene URL, daher zwingend ein Klick statt eines zweiten Deep-Links).
  3. Rueckflug-Ergebnisliste parsen, guenstigste waehlen.

Datenqualitaet: Preis/Airlines/Stopp-Zahl/Layover-FLUGHAFEN sind ECHT (aus
dem gerenderten Kartentext, siehe ``_CARD``-Regex). Die einzelnen Segment-
ZEITEN zwischen den Stopps sieht man in dieser Listenansicht nicht (nur
Gesamt-Abflug/-Ankunft + Gesamtdauer) - werden daher gleichmaessig auf die
Gesamtreisezeit verteilt und das Angebot mit
``FlightOffer.segment_times_approximate = True`` markiert.
``logic/route_filter.py`` ueberspringt dafuer den minutengenauen Layover-
Check, prueft Stopp-Zahl/Zeitfenster/Geometrie aber normal weiter (die
basieren auf echten Werten).
"""
from __future__ import annotations

import re
from datetime import date

from ..config import Config
from ..logging_setup import get_logger
from ..offers import FlightOffer
from .flight_cards import build_approx_segments, parse_cards
from .flights import SEARCH_PAX, _make_query, _out_leg, _ret_leg
from .scraper_base import (
    browser_page,
    dismiss_consent,
    expand_more_results,
    goto,
    save_screenshot,
    wait_for_stable_result_count,
)

log = get_logger("source.flights_mc_browser")
SOURCE = "google_flights(playwright-multicity)"

# Eine Ergebniskarte in der Google-Flights-Listenansicht, als Textblock:
#   15:15 \n – \n 17:45+1 \n Airline1, Airline2 \n 21 Std. 30 Min. \n
#   STR–USM \n 2 Stopps \n VIE, BKK \n 668 kg CO2e \n ... \n 1.369 € \n
#   gesamte Reise
# (Parsing/Segment-Aufbau: siehe flight_cards.py - geteilt mit
# flights_group_browser.py, das dieselben Karten fuer Round-Trip liest.)
_END_MARKER = "gesamte Reise"


def _parse_cards(body: str) -> list[dict]:
    return parse_cards(body, _END_MARKER)


async def _wait_for_cards(page) -> None:
    import contextlib
    with contextlib.suppress(Exception):
        await page.wait_for_function(
            "document.body.innerText.includes('gesamte Reise') || "
            "document.body.innerText.includes('Keine Ergebnisse') || "
            "document.body.innerText.includes('0 Ergebnisse')",
            timeout=25000,
        )
    # Nicht mehr fest 1.2s warten (13.09.26 Nutzervorgabe: reichte nicht
    # zuverlaessig) - stattdessen warten, bis sich die Kartenzahl 2s lang
    # nicht mehr aendert, dann "Mehr Fluege ansehen" aufklappen.
    await wait_for_stable_result_count(page, "gesamte Reise")
    await expand_more_results(page)
    await wait_for_stable_result_count(page, "gesamte Reise")


async def _search_one(cfg: Config, origin: str, dest: str, out_d: date,
                      ret_d: date, pax: int = SEARCH_PAX) -> FlightOffer | None:
    """``pax`` steuert die ECHTE Suchanfrage: Standard (SEARCH_PAX=1) liefert
    eine Hochrechnung wie bisher, ``pax=cfg.trip.persons`` fragt Google
    direkt mit der vollen Personenzahl (14.09.26, externe Review Punkt A3:
    Multi-City bekam bisher NIE einen echten Gruppen-Check - eine 1-Pax-
    Hochrechnung konkurrierte im Verdict direkt mit bestaetigten Round-Trip-
    Preisen)."""
    q, url = _make_query([_out_leg(origin, out_d, cfg), _ret_leg(dest, ret_d, cfg)],
                         "multi-city", cfg, pax=pax)
    # Der Link fuer den Nutzer soll immer die echte Personenzahl zeigen
    # (Nutzer-Fund 13.09.26: "Links fuehren immer noch zu Ergebnissen fuer
    # eine Person") - zweiter, rein lokal kodierter Query, kein Extra-Request.
    _, url_group = _make_query([_out_leg(origin, out_d, cfg), _ret_leg(dest, ret_d, cfg)],
                               "multi-city", cfg, pax=cfg.trip.persons)
    async with browser_page(cfg) as page:
        # Gleicher Consent-Cookie-Trick wie primp in sources/flights.py -
        # spart im besten Fall den ganzen consent.google.com-Umweg.
        import contextlib
        with contextlib.suppress(Exception):
            await page.context.add_cookies([{"name": "SOCS", "value": "CAI",
                                             "domain": ".google.com", "path": "/"}])
        await goto(page, url)
        await dismiss_consent(page)
        await _wait_for_cards(page)
        out_body = await page.inner_text("body")
        out_cards = _parse_cards(out_body)
        if not out_cards:
            if cfg.sources.scraper.screenshot_on_error:
                await save_screenshot(page, SOURCE)
            return None
        out_cards.sort(key=lambda c: c["price"])
        cheapest = out_cards[0]

        # Der Preis steht nur im aria-label ("Gesamtpreis ab 1369 Euro..."),
        # NICHT im sichtbaren Linktext - get_by_role/name (Accessible Name),
        # nicht get_by_text (sichtbarer Text), sonst findet Playwright nichts.
        price_int = round(cheapest["price"])
        link = page.get_by_role("link", name=re.compile(rf"Gesamtpreis ab {price_int}\s*Euro")).first
        if not await link.count():
            # Rundungs-/Formatabweichung - ersten Ergebnislink nehmen.
            link = page.get_by_role("link", name=re.compile(r"Gesamtpreis ab")).first
        # force=True: ein verschachteltes Kind-Element (Gesamtdauer-Badge)
        # faengt Pointer-Events ab und blockiert sonst den normalen Klick.
        await link.click(timeout=8000, force=True)
        await _wait_for_cards(page)
        ret_body = await page.inner_text("body")
        ret_cards = _parse_cards(ret_body)
        if not ret_cards:
            if cfg.sources.scraper.screenshot_on_error:
                await save_screenshot(page, SOURCE)
            return None
        ret_cards.sort(key=lambda c: c["price"])
        ret_cheapest = ret_cards[0]

    out_segs = build_approx_segments(cheapest["origin"], cheapest["dest"], cheapest["layovers"],
                                     cheapest["dep_time"], cheapest["duration_min"], out_d)
    ret_segs = build_approx_segments(ret_cheapest["origin"], ret_cheapest["dest"], ret_cheapest["layovers"],
                                     ret_cheapest["dep_time"], ret_cheapest["duration_min"], ret_d)
    total = ret_cheapest["price"]  # "gesamte Reise" auf der Rueckflug-Karte ist der volle Endpreis
    persons = cfg.trip.persons
    airlines = list(dict.fromkeys([*cheapest["airlines"], *ret_cheapest["airlines"]]))
    if pax >= persons:
        # Echte Gruppensuche: "total" ist bereits der Gesamtpreis fuer alle
        # `pax` Personen, NICHT hochrechnen (gleiche Logik wie flights.py
        # _to_offer fuer pax_mode="group").
        price_total, price_per_person, pax_mode = round(total, 2), round(total / persons, 2), "group"
    else:
        price_total, price_per_person, pax_mode = round(total * persons, 2), round(total, 2), "estimated"
    return FlightOffer(
        source=SOURCE, direction="multi_city", trip_type="multi_city",
        origin=origin, destination=dest, search_date=out_d, return_date=ret_d,
        price_total=price_total, price_per_person=price_per_person,
        currency=cfg.trip.currency, airlines=airlines,
        segments=out_segs, return_segments=ret_segs,
        deep_link=url_group, pax_mode=pax_mode, segment_times_approximate=True,
    )


async def fetch(cfg: Config) -> tuple[list[FlightOffer], dict]:
    t, fs = cfg.trip, cfg.sources.flights
    if not fs.multicity or not fs.multicity_browser_fallback:
        return [], {"ok": True, "count": 0, "attempted": 0, "error": "deaktiviert"}
    import asyncio
    import random

    pairs = t.rt_date_pairs() if fs.multicity_all_date_pairs else t.rt_date_pairs()[:1]
    combos = [(a, b) for a in t.origin_airports for b in t.origin_airports if a != b]
    lo, hi = fs.request_delay_seconds
    offers: list[FlightOffer] = []
    attempted = failed = 0
    first_error = ""
    for i, (a, b) in enumerate(combos):
        for out_d, ret_d in pairs:
            attempted += 1
            label = f"MC(Browser) {a}->USM->{b} {out_d}/{ret_d}"
            try:
                off = await _search_one(cfg, a, b, out_d, ret_d)
                if off:
                    offers.append(off)
                    log.info("%s: %.0f EUR (%s / %s)", label, off.price_total,
                             "+".join(off.airlines), off.route)
                else:
                    log.info("%s: keine Ergebnisse lesbar", label)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                first_error = first_error or f"{label}: {type(exc).__name__}: {exc}"
                log.warning("%s: Fehler %s: %s", label, type(exc).__name__, exc)
        if i < len(combos) - 1:
            await asyncio.sleep(random.uniform(lo, hi))
    return offers, {
        "ok": failed == 0, "count": len(offers), "attempted": attempted,
        "failed": failed, "error": first_error,
        "note": "Browser-basierter Multi-City-Fallback (primp liefert hierfuer keine Daten, siehe Modul-Docstring)",
    }


def collect(cfg: Config) -> tuple[list[FlightOffer], dict]:
    """Synchroner Wrapper (gleiches Muster wie sources/hotels/__init__.py)."""
    from .scraper_base import playwright_available

    if not cfg.sources.flights.multicity_browser_fallback:
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
