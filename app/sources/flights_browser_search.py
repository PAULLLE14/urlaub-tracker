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

    _LINK_PATTERN = re.compile(r"Ab \d+\s*Euro für Hin- und Rückflug")

    async def _click_cheapest_card(page, *, select_tab: bool) -> bool:
        """Wartet auf die Kartenliste, klickt die ERSTE "Ab X Euro"-Karte.
        Gibt False zurueck, wenn nichts klickbar war.

        Nutzer-Fund 17.09.26 (isoliert per Debug-Skript nachgestellt, siehe
        Git-Historie): die vorherige Version rief ``select_cheapest_tab()``
        (den "Am guenstigsten"-Tab-Klick) fuer BEIDE Etappen auf - Hinflug-
        UND Rueckflug-Auswahl. Auf der Rueckflug-Seite gibt es aber keinen
        entsprechenden Tab mehr; der Klick traf dort vermutlich ein anderes
        Element und brachte die Seite in einen Zustand, aus dem sie sich nie
        wieder erholte (haengte dauerhaft bei "Preise werden abgerufen").
        Drei identische Testlaeufe mit ``select_tab=False`` fuer die zweite
        Etappe liefen dagegen alle zuverlaessig durch (~5-10s bis zu den
        Buchungsoptionen). Deshalb: Tab-Klick NUR fuer die erste (Hinflug-)
        Karte, siehe Aufrufer unten. Bewusst feste Wartezeiten statt
        Marker-basiertem ``wait_for_stable_result_count`` - auch das war
        Teil der urspruenglich fehlschlagenden Version."""
        await page.wait_for_timeout(3000)
        if select_tab:
            await select_cheapest_tab(page)
            await page.wait_for_timeout(8000)
        link = page.get_by_role("link", name=_LINK_PATTERN)
        if not await link.count():
            return False
        # force=True: wie beim ITA-Matrix-/Multi-City-Kartenklick - ein
        # verschachteltes Kind-Element faengt sonst Pointer-Events ab.
        await link.first.click(timeout=8000, force=True)
        return True

    async with browser_page(cfg) as page:
        with contextlib.suppress(Exception):
            await page.context.add_cookies([{"name": "SOCS", "value": "CAI",
                                             "domain": ".google.com", "path": "/"}])
        await goto(page, url)
        await dismiss_consent(page)
        if not await _click_cheapest_card(page, select_tab=True):
            if cfg.sources.scraper.screenshot_on_error:
                await save_screenshot(page, SOURCE + "-booking-options-out")
            return None
        if not await _click_cheapest_card(page, select_tab=False):
            if cfg.sources.scraper.screenshot_on_error:
                await save_screenshot(page, SOURCE + "-booking-options-ret")
            return None
        # Live gemessen (17.09.26): die Buchungsoptionen brauchten mal 5s,
        # mal 30s (Streuung, keine feste Ladezeit) - grosszuegiges Timeout
        # + Polling statt eines knappen Fixwerts, der genau diese 30s-Faelle
        # verpasst haette.
        final_body = ""
        for _ in range(12):
            with contextlib.suppress(Exception):
                final_body = await page.inner_text("body")
            if "Buchungsoptionen" in final_body:
                break
            await page.wait_for_timeout(5000)
        result = parse_booking_options(final_body)
        if not result["options"]:
            if cfg.sources.scraper.screenshot_on_error:
                await save_screenshot(page, SOURCE + "-booking-options-empty")
            return None
        return result
