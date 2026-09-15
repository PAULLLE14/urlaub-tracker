"""Echte Round-Trip-Preissuche via Playwright, fuer beliebige Personenzahl.

Zentrale, wiederverwendbare Grundlage fuer zwei Verbraucher:
  * ``flights_group_browser.py`` - echte 8-/4-Pax-Preise fuer die guenstigsten
    Kandidaten verifizieren.
  * ``flights.py`` - Fallback, wenn primp fuer eine Kombi wiederholt mit
    einem der bekannten transienten Parser-Fehler scheitert (verifiziert
    11.09.26 fuer STR/MUC: ``TypeError: payload[3] is None``, obwohl echte
    Fluege existieren - siehe ``flights.py`` ``_RETRYABLE_PARSE_ERRORS``).
    14.09.26 (externe Review, Punkt A4): bisher wurde so ein Fehlschlag nur
    als ``failed_query`` geloggt, STR/MUC fielen dadurch komplett aus dem
    Vergleich. Der Browser-Weg (wie beim Gruppen-Check) liefert dieselbe
    Route zuverlaessig.

In eigenem Modul statt in ``flights_group_browser.py``, damit ``flights.py``
es importieren kann, OHNE einen Zirkelimport zu erzeugen (``flights.py``
definiert ``_make_query``/``_out_leg``/``_ret_leg``, die hier gebraucht
werden - waeren diese Funktionen umgekehrt in ``flights_group_browser.py``,
haette ``flights.py`` nicht importieren koennen, ohne dass sich beide
Module gegenseitig importieren).
"""
from __future__ import annotations

import contextlib
from datetime import date

from ..config import Config
from ..logging_setup import get_logger
from .flight_cards import confirmed_dates, parse_cards
from .scraper_base import (
    browser_page,
    dismiss_consent,
    expand_more_results,
    goto,
    save_screenshot,
    wait_for_stable_result_count,
)

log = get_logger("source.flights_browser_search")
SOURCE = "google_flights(playwright-roundtrip)"
_END_MARKER = "Hin und zurück"


async def cheapest_round_trip_cards(cfg: Config, origin: str, out_d: date, ret_d: date,
                                    pax: int) -> tuple[list[dict], str]:
    """Alle Ergebniskarten (samt Preis, Airlines, Zeiten, Stopps, Layover -
    siehe ``flight_cards.parse_cards``) fuer eine echte Round-Trip-Suche mit
    `pax` Passagieren. Gibt (Karten, Deep-Link) zurueck, ([], url) wenn
    nichts lesbar war."""
    from .flights import _make_query, _out_leg, _ret_leg  # lokaler Import: Zirkelimport vermeiden

    legs = [_out_leg(origin, out_d, cfg), _ret_leg(origin, ret_d, cfg)]
    q, url = _make_query(legs, "round-trip", cfg, pax=pax)
    async with browser_page(cfg) as page:
        with contextlib.suppress(Exception):
            await page.context.add_cookies([{"name": "SOCS", "value": "CAI",
                                             "domain": ".google.com", "path": "/"}])
        await goto(page, url)
        await dismiss_consent(page)
        with contextlib.suppress(Exception):
            await page.wait_for_function(
                f"document.body.innerText.includes('{_END_MARKER}') || "
                "document.body.innerText.includes('Keine Ergebnisse')",
                timeout=25000,
            )
        await wait_for_stable_result_count(page, _END_MARKER)
        await expand_more_results(page)
        await wait_for_stable_result_count(page, _END_MARKER)
        body = ""
        with contextlib.suppress(Exception):
            body = await page.inner_text("body")
        # Roadmap Runde 2/3, Punkt 2.3/6.4: Google's eigener "Preise
        # beobachten"-Text bestaetigt (oder widerlegt) in maschinenlesbarer
        # Form, dass die Ergebnisliste wirklich zu den angefragten Daten
        # gehoert - siehe flight_cards.confirmed_dates(). Weicht sie ab, sind
        # die Karten-Preise fuer ANDERE Daten (URL-Parameter griffen nicht),
        # dann lieber gar keine Karten zurueckgeben als einen falsch
        # datierten Preis als "echt geprueft" durchreichen.
        confirmed = confirmed_dates(body)
        if confirmed is not None and confirmed != (out_d.isoformat(), ret_d.isoformat()):
            log.warning("%s %s %s/%s: Google zeigt Daten %s statt der angefragten "
                       "(%s/%s) - Ergebnisse verworfen, URL-Datumsparameter "
                       "griffen vermutlich nicht", SOURCE, origin, out_d, ret_d,
                       confirmed, out_d.isoformat(), ret_d.isoformat())
            if cfg.sources.scraper.screenshot_on_error:
                await save_screenshot(page, SOURCE)
            return [], url
        cards = parse_cards(body, _END_MARKER)
        if not cards and cfg.sources.scraper.screenshot_on_error:
            await save_screenshot(page, SOURCE)
        return cards, url
