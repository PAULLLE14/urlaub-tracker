"""Echte Gruppen-/Split-Preis-Verifizierung via Playwright (NICHT primp).

Kritischer Fund (13.09.26, waehrend eines manuellen Gegenchecks auf
Nutzerwunsch): primp (reiner HTTP-Request, siehe ``sources/flights.py``)
liefert fuer Mehrpersonen-Suchen (``Passengers(adults=N)`` mit N>1) oft nur
einen BRUCHTEIL der echten Ergebnisse. Live reproduziert: eine echte
8-Pax-Suche FRA-USM 14.-28.05.2027 zeigte per primp nur 1 Treffer (Condor,
9.200 EUR) - ein echter Browser (dieselbe Suche, dieselbe Route/Datum) zeigt
SOFORT 2 Treffer, darunter Qatar Airways ab 7.939 EUR (den tatsaechlich
guenstigsten bestaetigten Preis fuer 8 Personen). Der alte primp-basierte
Gruppen-Check haette dadurch faelschlich eine echte, verfuegbare, 1.261 EUR
guenstigere Buchung als "nicht fuer 8 Personen verfuegbar" ausgeschlossen -
das genaue Gegenteil dessen, wofuer der Gruppen-Check gebaut wurde. Gleiche
Fehlerklasse wie das primp-Problem bei Multi-City (siehe
flights_multicity_browser.py): Google berechnet komplexere Suchen
offenbar teilweise asynchron nach, ein einzelner synchroner HTTP-Request
bekommt nur einen Teil-Stand.

Deshalb: fuer die ``group_check_top_n`` guenstigsten Round-Trip-Kombinationen
(siehe ``sources/flights.py`` ``collect_flights()``, das nur noch die
Kandidaten auswaehlt) wird hier zusaetzlich ein ECHTER Browser genutzt, um
die tatsaechlich guenstigsten Preise fuer die volle Personenzahl UND fuer
Split-Ticket-Formen zu finden. Bei Round-Trip zeigt Google (anders als bei
Multi-City) den vollen Hin+Rueck-Preis direkt in EINER Ergebnisliste - kein
zweiter Klick noetig, daher deutlich billiger als der Multi-City-Weg.

Split-Check (13.09.26 erweitert, Nutzervorgabe): nicht mehr nur 4+4, sondern
alle Partitionen der Personenzahl in 2er-/4er-Teile (bei 8 Personen also
4+4, 4+2+2 und 2+2+2+2) - eine knappe guenstige Tarifklasse kann von
mehreren kleineren Teilbuchungen oefter getroffen werden als von einer
grossen. Bewusst NUR innerhalb derselben Route/Datum gemischt (kein
airport-/datumsuebergreifendes Splitten in EINEM Angebot - das braeuchte
ein Mehr-Routen-Datenmodell, das FlightOffer aktuell nicht abbildet -
offener Punkt fuer eine spaetere Erweiterung).

Multi-City-Kombinationen werden hier NICHT geprueft (primp findet dafuer
ohnehin nie Kandidaten, siehe flights_multicity_browser.py fuer den
eigenen, dortigen 2-Klick-Preis - eine echte Gruppen-Verifikation dafuer
waere ein sinnvoller naechster Schritt, aber vorerst nicht umgesetzt).
"""
from __future__ import annotations

import re
from datetime import date

from ..config import Config
from ..logging_setup import get_logger
from ..offers import FlightOffer
from .room_split import partitions
from .flights import _flag_duplicate_estimates, _make_query, _out_leg, _ret_leg
from .scraper_base import (
    browser_page,
    dismiss_consent,
    expand_more_results,
    goto,
    parse_money,
    save_screenshot,
    wait_for_stable_result_count,
)

log = get_logger("source.flights_group_browser")
SOURCE = "google_flights(playwright-group)"

# Wie _CARD in flights_multicity_browser.py, aber Round-Trip-Karten enden
# auf "Hin und zurück" statt "gesamte Reise".
_CARD_RT = re.compile(
    r"(?P<dep>\d{1,2}:\d{2})\s*\n\s*–\s*\n\s*"
    r"(?P<arr>\d{1,2}:\d{2})(?:\+(?P<arr_days>\d+))?\s*\n\s*"
    r"(?P<airlines>[^\n]+?)\s*\n\s*"
    r"(?P<duration>(?:\d+\s*Std\.?\s*)?(?:\d+\s*Min\.?)?)\s*\n\s*"
    r"(?P<origin>[A-Z]{3})–(?P<dest>[A-Z]{3})\s*\n\s*"
    r"(?P<stops>Nonstop|Direkt|\d+\s*Stopps?)\s*\n\s*"
    r"(?:(?P<layover_line>[^\n]*[A-Z]{3}[^\n]*)\s*\n\s*)?"
    r".*?(?P<price>[\d.,]+)\s*€\s*\n\s*Hin und zurück",
    re.S,
)


def _parse_rt_prices(body: str) -> list[float]:
    out = []
    for m in _CARD_RT.finditer(body):
        p = parse_money(m.group("price"))
        if p:
            out.append(p)
    return out


async def _cheapest_real_price(cfg: Config, origin: str, out_d: date, ret_d: date,
                                pax: int) -> tuple[float | None, str]:
    """Echter guenstigster Round-Trip-Preis fuer `pax` Passagiere. Gibt
    (Preis-fuer-alle-`pax`-Personen, deep_link) zurueck, (None, url) wenn
    nichts lesbar war."""
    legs = [_out_leg(origin, out_d, cfg), _ret_leg(origin, ret_d, cfg)]
    q, url = _make_query(legs, "round-trip", cfg, pax=pax)
    async with browser_page(cfg) as page:
        import contextlib
        with contextlib.suppress(Exception):
            await page.context.add_cookies([{"name": "SOCS", "value": "CAI",
                                             "domain": ".google.com", "path": "/"}])
        await goto(page, url)
        await dismiss_consent(page)
        with contextlib.suppress(Exception):
            await page.wait_for_function(
                "document.body.innerText.includes('Hin und zurück') || "
                "document.body.innerText.includes('Keine Ergebnisse')",
                timeout=25000,
            )
        # Nicht mehr fest 1.2s warten (13.09.26 Nutzervorgabe) - stattdessen
        # bis die Kartenzahl 2s stabil bleibt, dann "Mehr Fluege" aufklappen.
        await wait_for_stable_result_count(page, "Hin und zurück")
        await expand_more_results(page)
        await wait_for_stable_result_count(page, "Hin und zurück")
        body = ""
        with contextlib.suppress(Exception):
            body = await page.inner_text("body")
        prices = _parse_rt_prices(body)
        if not prices:
            if cfg.sources.scraper.screenshot_on_error:
                await save_screenshot(page, SOURCE)
            return None, url
        return min(prices), url


async def verify(cfg: Config, offers: list[FlightOffer]) -> dict:
    """Prueft die guenstigsten Round-Trip-Kombinationen aus `offers` echt
    nach (Gruppen- UND Split-Preis) und mutiert `offers` in-place:
      - bestaetigt eine echte Gruppensuche den 1-Pax-hochgerechneten Preis
        NICHT, wird die Hochrechnung (und alle identischen Duplikate, siehe
        _flag_duplicate_estimates) als ``group_check_unconfirmed`` markiert.
      - findet die echte Gruppensuche einen ECHTEN Preis, wird dieser als
        eigenes Angebot (pax_mode="group") angehaengt - unabhaengig davon,
        ob er die Hochrechnung bestaetigt oder unterbietet.
      - eine guenstigere 2x(persons/2)-Aufteilung wird als eigenes Angebot
        (pax_mode="split_half") angehaengt.
    """
    t, fs = cfg.trip, cfg.sources.flights
    if not fs.group_check or t.persons <= 1:
        return {"ok": True, "group_checked": 0, "split_checked": 0, "attempted": 0}

    candidates = [o for o in offers if o.trip_type == "round_trip" and not o.excluded]
    best_by_key: dict[tuple, FlightOffer] = {}
    for o in candidates:
        key = (o.trip_type, o.origin, o.destination, o.search_date, o.return_date)
        if key not in best_by_key or o.price_total < best_by_key[key].price_total:
            best_by_key[key] = o
    top_keys = sorted(best_by_key, key=lambda k: best_by_key[k].price_total)[: fs.group_check_top_n]

    # Split-Ticket-Formen: alle Partitionen der Personenzahl in 2er-/4er-
    # Teile (13.09.26 Nutzervorgabe: nicht nur 4+4, auch 2+2+2+2 und 4+2+2 -
    # eine knappe guenstige Tarifklasse kann so oefter getroffen werden).
    # Bewusst auf dieselbe Route/Datum beschraenkt (kein Mix verschiedener
    # Abflughaefen/Termine in EINEM Angebot - das bräuchte ein Mehr-Routen-
    # Datenmodell, siehe Modul-Docstring fuer den offenen Punkt).
    split_shapes = [p for p in partitions(t.persons, 4, 2) if all(s in (2, 4) for s in p)]
    split_sizes = sorted({s for shape in split_shapes for s in shape})
    group_checked = split_checked = 0
    errors: list[str] = []

    for key in top_keys:
        _trip_type, origin, _destination, s_date, r_date = key
        estimated = best_by_key[key]
        label = f"GROUP({t.persons}) {origin} {s_date}/{r_date}"
        group_checked += 1
        try:
            cheapest_group, deep = await _cheapest_real_price(cfg, origin, s_date, r_date, t.persons)
            if cheapest_group is None:
                log.warning("%s: kein echter Preis lesbar - Hochrechnung bleibt "
                           "unbestaetigt, aber nicht ausgeschlossen", label)
            else:
                log.info("%s: echter Preis %.0f EUR (Hochrechnung war %.0f EUR)",
                         label, cheapest_group, estimated.price_total)
                offers.append(FlightOffer(
                    source=SOURCE, direction="round_trip", trip_type="round_trip",
                    origin=origin, destination=t.destination_airport,
                    search_date=s_date, return_date=r_date,
                    price_total=round(cheapest_group, 2),
                    price_per_person=round(cheapest_group / t.persons, 2),
                    currency=t.currency, airlines=list(estimated.airlines),
                    segments=list(estimated.segments), deep_link=deep, pax_mode="group",
                ))
                if cheapest_group > estimated.price_total:
                    reason = (f"gruppen_check: fuer {t.persons} Personen nicht in diesem "
                             f"Preis verfuegbar (echter Gruppenpreis ab {cheapest_group:.0f} EUR)")
                    _flag_duplicate_estimates(offers, key, estimated.price_total, reason)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{label}: {type(exc).__name__}: {exc}")
            log.warning("%s: Fehler %s: %s", label, type(exc).__name__, exc)

        if split_shapes:
            price_by_size: dict[int, float] = {}
            deep_by_size: dict[int, str] = {}
            for size in split_sizes:
                label_s = f"SPLIT({size}) {origin} {s_date}/{r_date}"
                split_checked += 1
                try:
                    p, deep = await _cheapest_real_price(cfg, origin, s_date, r_date, size)
                    if p is None:
                        log.info("%s: kein echter Preis lesbar", label_s)
                    else:
                        price_by_size[size] = p
                        deep_by_size[size] = deep
                        log.info("%s: %.0f EUR", label_s, p)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{label_s}: {type(exc).__name__}: {exc}")
                    log.warning("%s: Fehler %s: %s", label_s, type(exc).__name__, exc)

            # Guenstigste Form waehlen (4+4 vs. 4+2+2 vs. 2+2+2+2) - nur
            # Formen, fuer die ALLE Teilgroessen einen Preis haben.
            best_shape, best_total = None, None
            for shape in split_shapes:
                if not all(s in price_by_size for s in shape):
                    continue
                total = sum(price_by_size[s] for s in shape)
                if best_total is None or total < best_total:
                    best_shape, best_total = shape, total

            if best_shape:
                shape_label = "+".join(str(s) for s in best_shape)
                mode = "split_" + "_".join(str(s) for s in best_shape)
                deep = deep_by_size.get(best_shape[0], "")
                log.info("SPLIT-BESTE Form (%s) %s %s/%s: %.0f EUR gesamt "
                        "(Hochrechnung war %.0f EUR)", shape_label, origin, s_date, r_date,
                        best_total, estimated.price_total)
                offers.append(FlightOffer(
                    source=SOURCE, direction="round_trip", trip_type="round_trip",
                    origin=origin, destination=t.destination_airport,
                    search_date=s_date, return_date=r_date,
                    price_total=round(best_total, 2),
                    price_per_person=round(best_total / t.persons, 2),
                    currency=t.currency, airlines=list(estimated.airlines),
                    segments=list(estimated.segments), deep_link=deep, pax_mode=mode,
                ))

    return {
        "ok": not errors, "group_checked": group_checked, "split_checked": split_checked,
        "attempted": len(top_keys), "error": "; ".join(errors[:3]),
    }


def collect(cfg: Config, offers: list[FlightOffer]) -> dict:
    """Synchroner Wrapper (gleiches Muster wie sources/hotels/__init__.py).
    Mutiert `offers` in-place (siehe verify())."""
    from .scraper_base import playwright_available

    ok, err = playwright_available()
    if not ok:
        return {"ok": False, "group_checked": 0, "split_checked": 0, "attempted": 0, "error": err}
    import asyncio

    try:
        return asyncio.run(verify(cfg, offers))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(verify(cfg, offers))
        finally:
            loop.close()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "group_checked": 0, "split_checked": 0, "attempted": 0,
                "error": f"{type(exc).__name__}: {exc}"}
