"""Echte Gruppen-/Split-Preis-Verifikation via Playwright (NICHT primp).

Kritischer Fund (13.09.26, waehrend eines manuellen Gegenchecks auf
Nutzerwunsch): primp (reiner HTTP-Request, siehe ``sources/flights.py``)
liefert fuer Mehrpersonen-Suchen (``Passengers(adults=N)`` mit N>1) oft nur
einen BRUCHTEIL der echten Ergebnisse. Live reproduziert: eine echte
8-Pax-Suche FRA-USM 14.-28.05.2027 zeigte per primp nur 1 Treffer (Condor,
9.200 EUR) - ein echter Browser (dieselbe Suche, dieselbe Route/Datum) zeigt
SOFORT 2 Treffer, darunter Qatar Airways ab 7.939 EUR (den tatsaechlich
guenstigsten bestaetigten Preis fuer 8 Personen). Deshalb: fuer die
``group_check_top_n`` guenstigsten Round-Trip-Kombinationen UND ALLE
Multi-City-Kombinationen (siehe ``sources/flights.py`` ``collect_flights()``,
das nur noch die Kandidaten auswaehlt) wird hier ein echter Browser genutzt -
keine Schaetzungen, nur harte Pruefungen (14.09.26 Nutzervorgabe).

14.09.26 (externe Code-Review) - drei Korrekturen:

A1 (falscher Flug): der Gruppen-Check las frueher NUR ``min(prices)`` aus
der 8-Pax-Karte, das Angebot bekam aber Airlines/Segmente vom 1-Pax-
Schaetzangebot geliehen. War der guenstigste 8-Pax-Flug ein ANDERER als der
guenstigste 1-Pax-Flug (z.B. Condor statt Qatar), zeigte das Dashboard eine
falsche Airline/Zeiten-Preis-Kombination - UND ``apply_constraints()``
pruefte die falschen Segmentdaten (ein Austrian-Flug oder ein Abflug vor
16:30 haette so durchrutschen koennen). Jetzt liest ``flight_cards.py`` die
KOMPLETTE Karte (Preis + Airlines + Zeiten + Stopps + Layover) der
guenstigsten 8-Pax-Karte, das Angebot bekommt seine EIGENEN (approximierten,
``segment_times_approximate=True``) Segmente.

A2 (Split-Preis war eine Untergrenze, keine Buchung): der alte Split-Check
summierte Preise verschiedener Zimmergroessen (2/4) aus ``partitions()`` -
zwei 4er-Suchen treffen aber oft denselben knappen Tarif-Bucket; wenn der nur
5 Plaetze hat, zeigt JEDE 4er-Suche den billigen Preis, obwohl nur EINE
Buchung davon wirklich billig waere. Nutzervorgabe 14.09.26: die Gruppe
besteht aus 2 Familien a 4 Personen - deshalb NUR noch 4+4 (kein
``partitions()`` mehr fuer Fluege), UND der Preis wird nicht mehr blind
verdoppelt:
  - Preis(8)/8 nahe an Preis(4)/4 (<=5%)  -> Bucket reicht fuer alle 8,
    kein Split-Angebot noetig (der echte 8er-Preis ist bereits das beste
    Angebot).
  - Preis(8)/8 deutlich hoeher            -> Bucket hat vermutlich nur 4-7
    Plaetze. Split-Angebot = 4x Preis(4)/4 (1. Familie sicher billig) +
    4x Preis(8)/8 (2. Familie konservativ zum vollen 8er-Preis) - eine
    OBERGRENZE, nie als "bestaetigt" markiert (``pax_mode="split_4_4"`` hat
    IMMER einen erklaerenden ``price_confidence``-Text, siehe offers.py).
  - Kein 8er-Preis lesbar                 -> nur die alte Preis-UNTERGRENZE
    (2x Preis(4)) mit explizitem "nur Untergrenze"-Hinweis.

A3 (Multi-City ohne Gruppen-Check): ``flights_multicity_browser.py`` lieferte
bisher nur ``pax_mode="estimated"``, das konkurrierte im Verdict direkt mit
bestaetigten Round-Trip-Preisen. Jetzt bekommt JEDE Multi-City-Kombination
eine echte 8-Pax-Suche (ueber ``flights_multicity_browser._search_one``) -
kein Top-N-Limit (14.09.26 Nutzervorgabe: keine Schaetzungen, nur harte
Pruefungen).

B (Blockrisiko): der Split-Check (4-Pax-Suche) laeuft nur noch, wenn
Preis(8)/8 tatsaechlich deutlich (>5%) ueber dem 1-Pax-Schaetzpreis liegt -
sonst gibt es vermutlich keinen knappen Bucket, ein Split wuerde ohnehin
nichts bringen.

14.09.26 (Nutzerkorrektur): ein fruehes Ueberspringen von Kandidaten anhand
ihrer 1-Pax-Schaetzung (ausprobiert, dann wieder entfernt) war falsch - 1-Pax-
Preis und 8-Pax-Bucket-Verfuegbarkeit korrelieren nicht zuverlaessig genug,
um eine Route ungeprueft auszuschliessen (das ist ja der ganze Grund, warum
dieser Check existiert: eine Route mit mittelmaessigem 1-Pax-Preis kann fuer
8 Personen trotzdem die guenstigste sein, oder umgekehrt nur noch fuer
weniger als 8 Personen verfuegbar). JEDE Top-N-Kombination wird immer real
geprueft, ohne Abkuerzung.
"""
from __future__ import annotations

from datetime import date

from ..config import Config
from ..logging_setup import get_logger
from ..offers import FlightOffer
from .flight_cards import build_approx_segments, cheapest_card, implausible_price_reason
from .flights import _ConsentFetcher, _flag_duplicate_estimates, cheapest_price_no_bag
from .flights_browser_search import cheapest_round_trip_cards
from .flights_multicity_browser import _search_one as _mc_search_one

log = get_logger("source.flights_group_browser")
SOURCE = "google_flights(playwright-group)"

_SPLIT_SIZE = 4  # Nutzervorgabe 14.09.26: 2 Familien a 4 Personen, fest.
_SAME_BUCKET_RATIO = 1.05   # Preis(8)/8 <= Preis(4)/4 * diesen Faktor -> ein Bucket reicht
_SPLIT_WORTHWHILE_RATIO = 1.05  # Preis(8)/8 muss mehr als 5% ueber der 1-Pax-Schaetzung liegen
_PRICE_LADDER_TOP_N = 5  # Roadmap Runde 2, Punkt 2.1: nur fuer die Top-5-Kombis (extra Request je Kombi)


def _key_of(o: FlightOffer) -> tuple:
    return (o.trip_type, o.origin, o.destination, o.search_date, o.return_date)


def _best_by_key(offers: list[FlightOffer]) -> dict[tuple, FlightOffer]:
    best: dict[tuple, FlightOffer] = {}
    for o in offers:
        key = _key_of(o)
        if key not in best or o.price_total < best[key].price_total:
            best[key] = o
    return best


def _offer_from_card(card: dict, *, cfg: Config, origin: str, s_date: date, r_date: date,
                     pax_mode: str, deep_link: str, price_total: float,
                     price_per_person: float, price_confidence: str = "") -> FlightOffer:
    """Baut ein FlightOffer aus einer ECHTEN Ergebniskarte (siehe
    flight_cards.py) - Airlines/Stopps/Layover-Flughaefen sind real, die
    Segment-EINZELZEITEN approximiert (nur Gesamt-Abflug/-Ankunft/-Dauer
    sichtbar), deshalb ``segment_times_approximate=True`` (A1-Fix: nicht
    mehr die Segmente des 1-Pax-Schaetzangebots wiederverwenden)."""
    segs = build_approx_segments(card["origin"], card["dest"], card["layovers"],
                                 card["dep_time"], card["duration_min"], s_date)
    return FlightOffer(
        source=SOURCE, direction="round_trip", trip_type="round_trip",
        origin=origin, destination=cfg.trip.destination_airport,
        search_date=s_date, return_date=r_date,
        price_total=round(price_total, 2), price_per_person=round(price_per_person, 2),
        currency=cfg.trip.currency, airlines=card["airlines"], segments=segs,
        deep_link=deep_link, pax_mode=pax_mode, price_confidence=price_confidence,
        segment_times_approximate=True,
    )


async def verify(cfg: Config, offers: list[FlightOffer]) -> dict:
    """Prueft die guenstigsten Round-Trip- UND Multi-City-Kombinationen aus
    `offers` echt nach und mutiert `offers` in-place (siehe Modul-Docstring
    fuer die volle Logik). Gibt eine Zusammenfassung fuer Logs/Health zurueck."""
    t, fs = cfg.trip, cfg.sources.flights
    if not fs.group_check or t.persons <= 1:
        return {"ok": True, "group_checked": 0, "split_checked": 0, "attempted": 0}

    rt_best = _best_by_key([o for o in offers if o.trip_type == "round_trip" and not o.excluded])
    rt_keys = sorted(rt_best, key=lambda k: rt_best[k].price_total)[: fs.group_check_top_n]
    # 14.09.26 Nutzervorgabe: kein Top-N-Limit mehr fuer Multi-City - keine
    # Schaetzungen, nur harte Pruefungen (gleiche Begruendung wie beim
    # entfernten Ueberspringen bei Round-Trip: 1-Pax-Preis und 8-Pax-Bucket
    # korrelieren nicht zuverlaessig genug). ALLE Multi-City-Kombinationen
    # bekommen eine echte 8-Pax-Suche.
    mc_best = _best_by_key([o for o in offers if o.trip_type == "multi_city" and not o.excluded])
    mc_keys = sorted(mc_best, key=lambda k: mc_best[k].price_total)

    group_checked = split_checked = 0
    errors: list[str] = []
    no_bag_fetcher = _ConsentFetcher()

    for idx, key in enumerate(rt_keys):
        _trip_type, origin, _destination, s_date, r_date = key
        estimated = rt_best[key]
        label = f"GROUP({t.persons}) {origin} {s_date}/{r_date}"

        # 14.09.26 Nutzerkorrektur: KEIN Ueberspringen mehr anhand der
        # 1-Pax-Schaetzung, egal wie weit sie ueber dem bisher besten
        # bestaetigten Preis liegt - 1-Pax-Preis und 8-Pax-Bucket-
        # Verfuegbarkeit korrelieren nicht zuverlaessig (das ist ja der
        # ganze Grund, warum dieser Check ueberhaupt existiert: eine Route
        # kann trotz mittelmaessigem 1-Pax-Preis fuer 8 Personen noch genug
        # guenstige Sitze frei haben - oder umgekehrt nur noch fuer z.B. 5
        # Personen verfuegbar sein). JEDE Kombination wird real geprueft.
        group_checked += 1
        card8 = deep8 = None
        try:
            cards8, deep8 = await cheapest_round_trip_cards(cfg, origin, s_date, r_date, t.persons)
            card8 = cheapest_card(cards8)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{label}: {type(exc).__name__}: {exc}")
            log.warning("%s: Fehler %s: %s", label, type(exc).__name__, exc)

        if card8 is None:
            log.warning("%s: kein echter Preis lesbar - Hochrechnung bleibt "
                       "unbestaetigt, aber nicht ausgeschlossen", label)
        else:
            log.info("%s: echter Preis %.0f EUR (%s, Hochrechnung war %.0f EUR)",
                     label, card8["price"], "+".join(card8["airlines"]), estimated.price_total)
            group_off = _offer_from_card(
                card8, cfg=cfg, origin=origin, s_date=s_date, r_date=r_date,
                pax_mode="group", deep_link=deep8,
                price_total=card8["price"], price_per_person=card8["price"] / t.persons,
            )
            suspect = implausible_price_reason(group_off.price_per_person)
            if suspect:
                group_off.group_check_unconfirmed = suspect
                log.warning("%s: %s", label, suspect)
            if idx < _PRICE_LADDER_TOP_N:
                group_off.price_ladder["1_pax_mit_gepaeck"] = round(estimated.price_per_person, 2)
                group_off.price_ladder["8_pax"] = round(group_off.price_per_person, 2)
                no_bag = cheapest_price_no_bag(cfg, no_bag_fetcher, origin, s_date, r_date)
                if no_bag is not None:
                    group_off.price_ladder["1_pax_ohne_gepaeck"] = round(no_bag, 2)
                else:
                    log.info("%s: Preis-Leiter ohne Gepaeck nicht lesbar", label)
            offers.append(group_off)
            if card8["price"] > estimated.price_total:
                reason = (f"gruppen_check: fuer {t.persons} Personen nicht in diesem "
                         f"Preis verfuegbar (echter Gruppenpreis ab {card8['price']:.0f} EUR)")
                _flag_duplicate_estimates(offers, key, estimated.price_total, reason)

        # Split-Check (A2+B): nur 4+4, nur wenn ein knapper Bucket zu
        # vermuten ist (Preis(8)/8 spuerbar ueber der 1-Pax-Schaetzung).
        do_split = (card8 is None or estimated.price_per_person <= 0
                   or (card8["price"] / t.persons) > estimated.price_per_person * _SPLIT_WORTHWHILE_RATIO)
        if not do_split:
            log.info("%s: Split-Check uebersprungen (8er-Preis/Pers. nah an 1-Pax-Schaetzung "
                     "- vermutlich kein knapper Bucket)", label)
            continue

        split_checked += 1
        try:
            cards4, deep4 = await cheapest_round_trip_cards(cfg, origin, s_date, r_date, _SPLIT_SIZE)
            card4 = cheapest_card(cards4)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"SPLIT({_SPLIT_SIZE}) {origin} {s_date}/{r_date}: {type(exc).__name__}: {exc}")
            log.warning("SPLIT(%d) %s %s/%s: Fehler %s: %s", _SPLIT_SIZE, origin, s_date, r_date,
                       type(exc).__name__, exc)
            card4 = deep4 = None

        if card4 is None:
            log.info("SPLIT(%d) %s %s/%s: kein echter Preis lesbar", _SPLIT_SIZE, origin, s_date, r_date)
            continue

        per_person4 = card4["price"] / _SPLIT_SIZE
        if card8 is not None and idx < _PRICE_LADDER_TOP_N:
            group_off.price_ladder["4_pax"] = round(per_person4, 2)
        lower_bound = card4["price"] * 2
        if card8 is not None:
            per_person8 = card8["price"] / t.persons
            ratio = per_person8 / per_person4 if per_person4 else None
            if ratio is not None and ratio <= _SAME_BUCKET_RATIO:
                log.info("SPLIT(%d) %s %s/%s: Bucket reicht vermutlich fuer alle %d "
                        "(Preis/Pers. 8er %.0f ~ 4er %.0f) - kein Split-Angebot noetig",
                        _SPLIT_SIZE, origin, s_date, r_date, t.persons, per_person8, per_person4)
                continue
            estimate_upper = _SPLIT_SIZE * per_person4 + _SPLIT_SIZE * per_person8
            confidence = (f"Schaetzung: {_SPLIT_SIZE} Plaetze zu {card4['price']:.0f} EUR sicher, "
                         f"weitere {_SPLIT_SIZE} vorsichtig zum vollen {t.persons}-Pax-Preis "
                         f"kalkuliert (2. Familie zuerst neu pruefen, bevor die 1. gebucht wird)")
            price_total = estimate_upper
        else:
            confidence = ("Nur Preis-UNTERGRENZE (2. Familie evtl. teurer) - kein echter "
                         f"{t.persons}-Pax-Preis zum Gegenpruefen lesbar")
            price_total = lower_bound

        log.info("SPLIT(%d) %s %s/%s: %.0f EUR (%s, %s)", _SPLIT_SIZE, origin, s_date, r_date,
                price_total, "+".join(card4["airlines"]), confidence)
        split_off = _offer_from_card(
            card4, cfg=cfg, origin=origin, s_date=s_date, r_date=r_date,
            pax_mode=f"split_{_SPLIT_SIZE}_{_SPLIT_SIZE}", deep_link=deep4,
            price_total=price_total, price_per_person=price_total / t.persons,
            price_confidence=confidence,
        )
        suspect = implausible_price_reason(card4["price"] / _SPLIT_SIZE)
        if suspect:
            split_off.group_check_unconfirmed = suspect
            log.warning("SPLIT(%d) %s %s/%s: %s", _SPLIT_SIZE, origin, s_date, r_date, suspect)
        offers.append(split_off)

    # Multi-City (A3): kein Split-Check (das 2-Klick-Routing macht eine
    # zusaetzliche 4-Pax-Suche unverhaeltnismaessig teuer) - nur die echte
    # 8-Pax-Bestaetigung fuer die guenstigsten Kandidaten.
    for key in mc_keys:
        _trip_type, origin, destination, s_date, r_date = key
        estimated = mc_best[key]
        label = f"GROUP-MC({t.persons}) {origin}->USM->{destination} {s_date}/{r_date}"
        group_checked += 1
        try:
            off = await _mc_search_one(cfg, origin, destination, s_date, r_date, pax=t.persons)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{label}: {type(exc).__name__}: {exc}")
            log.warning("%s: Fehler %s: %s", label, type(exc).__name__, exc)
            continue
        if off is None:
            log.warning("%s: kein echter Preis lesbar - Hochrechnung bleibt "
                       "unbestaetigt, aber nicht ausgeschlossen", label)
            continue
        log.info("%s: echter Preis %.0f EUR (Hochrechnung war %.0f EUR)",
                label, off.price_total, estimated.price_total)
        offers.append(off)
        if off.price_total > estimated.price_total:
            reason = (f"gruppen_check: fuer {t.persons} Personen nicht in diesem "
                     f"Preis verfuegbar (echter Gruppenpreis ab {off.price_total:.0f} EUR)")
            _flag_duplicate_estimates(offers, key, estimated.price_total, reason)

    return {
        "ok": not errors, "group_checked": group_checked, "split_checked": split_checked,
        "attempted": len(rt_keys) + len(mc_keys), "error": "; ".join(errors[:3]),
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
