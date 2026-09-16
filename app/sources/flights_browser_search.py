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
import re
from datetime import date

from ..config import Config
from ..logging_setup import get_logger
from .flight_cards import confirmed_dates, parse_booking_options, parse_cards
from .scraper_base import (
    browser_page,
    dismiss_consent,
    expand_more_results,
    goto,
    save_screenshot,
    select_cheapest_tab,
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
        # Nutzer-Fund 16.09.26: ohne diesen Klick bleiben wir auf "Beste
        # Fluege" (Preis+Komfort-Mix) - siehe select_cheapest_tab().
        await select_cheapest_tab(page)
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


async def cheapest_round_trip_booking_options(cfg: Config, origin: str, out_d: date,
                                              ret_d: date, pax: int) -> dict | None:
    """Klickt sich bis zu Googles finaler "Buchungsoptionen"-Seite durch
    (Hinflug waehlen -> Rueckflug waehlen - wie bei Multi-City, siehe
    ``flights_multicity_browser._search_one``) und liest ALLE Buchungs-
    optionen aus, nicht nur den Airline-Preis der Listenkarte.

    Live-Fund 16.09.26 (Nutzer-Screenshot): die Ergebniskarte zeigt nur
    den Preis der Fluggesellschaft direkt (Qatar Airways, 7.352 EUR) -
    Googles eigene Buchungsoptionen-Seite fuer DIESELBE Kombination zeigte
    lastminute.com fuer 7.080 EUR, fast 300 EUR guenstiger, als tatsaechlich
    niedrigsten "Gesamtpreis". ``flight_cards.parse_booking_options()``
    liest diese Liste (Airline + alle Drittanbieter, sortiert nach Preis).

    Nur fuer wenige Top-Kombinationen aufrufen (siehe Aufrufer) - kostet
    zwei zusaetzliche Klicks/Seitenladungen pro Kombi. Gibt None zurueck,
    wenn der Klick-Pfad an irgendeiner Stelle nicht durchlief (z.B. keine
    Ergebnisse mehr, Layout-Aenderung) - dann bleibt der schon bekannte
    Airline-Preis die einzige Zahl, kein Fehler fuers Gesamtergebnis."""
    from .flights import _make_query, _out_leg, _ret_leg

    legs = [_out_leg(origin, out_d, cfg), _ret_leg(origin, ret_d, cfg)]
    q, url = _make_query(legs, "round-trip", cfg, pax=pax)

    async def _select_cheapest_card(page) -> dict | None:
        """Wartet auf die Kartenliste, waehlt den "Am guenstigsten"-Tab,
        klickt die guenstigste Karte an. Gibt die geklickte Karte zurueck
        (fuers Logging) oder None, wenn nichts klickbar war."""
        with contextlib.suppress(Exception):
            await page.wait_for_function(
                f"document.body.innerText.includes('{_END_MARKER}') || "
                "document.body.innerText.includes('Keine Ergebnisse')",
                timeout=25000,
            )
        await wait_for_stable_result_count(page, _END_MARKER)
        await select_cheapest_tab(page)
        await wait_for_stable_result_count(page, _END_MARKER)
        body = ""
        with contextlib.suppress(Exception):
            body = await page.inner_text("body")
        cards = parse_cards(body, _END_MARKER)
        if not cards:
            return None
        cheapest = min(cards, key=lambda c: c["price"])
        price_int = round(cheapest["price"])
        # Playwright-Accessible-Name der Karte: "Ab 7352 Euro für Hin- und
        # Rückflug. Flug mit ..." - live verifiziert 16.09.26, identisches
        # Muster fuer Hin- UND Rueckflug-Auswahl.
        link = page.get_by_role("link", name=re.compile(
            rf"Ab {price_int}\s*Euro für Hin- und Rückflug"))
        if not await link.count():
            link = page.get_by_role("link", name=re.compile(
                r"Ab \d+\s*Euro für Hin- und Rückflug"))
        if not await link.count():
            return None
        # force=True: wie beim ITA-Matrix-/Multi-City-Kartenklick - ein
        # verschachteltes Kind-Element faengt sonst Pointer-Events ab.
        await link.first.click(timeout=8000, force=True)
        return cheapest

    async with browser_page(cfg) as page:
        with contextlib.suppress(Exception):
            await page.context.add_cookies([{"name": "SOCS", "value": "CAI",
                                             "domain": ".google.com", "path": "/"}])
        await goto(page, url)
        await dismiss_consent(page)
        out_card = await _select_cheapest_card(page)
        if out_card is None:
            if cfg.sources.scraper.screenshot_on_error:
                await save_screenshot(page, SOURCE + "-booking-options-out")
            return None
        ret_card = await _select_cheapest_card(page)
        if ret_card is None:
            if cfg.sources.scraper.screenshot_on_error:
                await save_screenshot(page, SOURCE + "-booking-options-ret")
            return None
        # Live-Fund 16.09.26 (zwei Debug-Screenshots, CheckRun #25 UND #26):
        # diese Seite blieb im Headless-Modus beide Male komplett bei "Preise
        # werden abgerufen" haengen - selbst nach 40s+ Wartezeit KEIN
        # Fortschritt (im interaktiven Test lief der Ladebalken dagegen
        # sichtbar durch, siehe "Ergebnisse werden abgerufen, 54%/87%/...").
        # Das ist eher ein haengengebliebener Ladezustand als "nur langsam" -
        # ein Reload nach der ersten Wartephase stoesst den Request oft neu
        # an (aehnliches Muster wie bei anderen haengenden SPA-Zustaenden).
        async def _has_options() -> bool:
            with contextlib.suppress(Exception):
                return bool(await page.evaluate(
                    "document.body.innerText.includes('Buchungsoptionen')"))
            return False

        with contextlib.suppress(Exception):
            await page.wait_for_function(
                "document.body.innerText.includes('Buchungsoptionen')", timeout=20000)
        if not await _has_options():
            log.info("BOOKING-OPTIONS: nach 20s noch 'Preise werden abgerufen' - Reload-Versuch")
            with contextlib.suppress(Exception):
                await page.reload(wait_until="domcontentloaded")
            with contextlib.suppress(Exception):
                await page.wait_for_function(
                    "document.body.innerText.includes('Buchungsoptionen')", timeout=25000)
        await wait_for_stable_result_count(page, "Weiter")
        await page.wait_for_timeout(1000)
        final_body = ""
        with contextlib.suppress(Exception):
            final_body = await page.inner_text("body")
        result = parse_booking_options(final_body)
        if not result["options"]:
            if cfg.sources.scraper.screenshot_on_error:
                await save_screenshot(page, SOURCE + "-booking-options-empty")
            return None
        return result
