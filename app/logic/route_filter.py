"""Harte Constraints auf Flugoptionen anwenden.

Markiert jede :class:`FlightOffer` mit ``excluded`` + ``exclude_reason`` statt
sie zu loeschen - so bleiben ausgefilterte Optionen im Dashboard sichtbar
(ausgegraut, mit Begruendung) und nachvollziehbar.

Regeln (aus config.flight_constraints):
  0. Ausgeschlossene Airlines (``excluded_airlines`` + optional
     ``exclude_gulf_carriers``) - Teilstring-Match auf die Airline-Liste.
  1. Abreisetag-Zeitfenster: erster Leg am Hinflug-Starttag darf nicht vor
     ``outbound_earliest_departure`` abheben. Gilt nur outbound, nur erster Leg.
  2. Max. Stopps pro Richtung (gesamt, nicht pro Teilstrecke).
  3. Unsinnige Umwege: Gesamt-Routendistanz > Faktor x Direktdistanz.
  4. Rueckwaerts-Routing: erster Hub liegt deutlich weiter vom Ziel weg als
     der Startflughafen (anderer Kontinent / falsche Richtung).
  5. Absurd lange Gesamtreisezeit.
  6. Umstieg zu knapp / Layover absurd lang.
"""
from __future__ import annotations

from ..config import Config
from ..geo import get_airport, haversine_km, route_distance_km
from ..logging_setup import get_logger
from ..offers import FlightOffer

log = get_logger("logic.route_filter")

# Golfstaaten-Carrier fuer den optionalen exclude_gulf_carriers-Schalter.
GULF_CARRIERS = ["Qatar Airways", "Etihad", "Emirates", "Saudia",
                 "Saudi Arabian Airlines", "Gulf Air", "Oman Air",
                 "Royal Jordanian", "flydubai", "Air Arabia", "Kuwait Airways"]


def _mark(off: FlightOffer, reason: str) -> None:
    off.excluded = True
    off.exclude_reason = reason


def _min_layover_for(hub: str, next_dest: str, fc) -> int:
    """BKK->USM (kurzer Bangkok-Airways-Zubringer, innerhalb Thailands) darf
    knapper sein als ein internationaler Umstieg - 13.09.26 Nutzervorgabe."""
    if hub == "BKK" and next_dest == "USM":
        return fc.min_layover_minutes_domestic_thailand
    return fc.min_layover_minutes_international


def _leg_route(segs: list) -> list[str]:
    if not segs:
        return []
    return [segs[0].from_airport] + [s.to_airport for s in segs]


def _geometry_violation(route: list[str], layovers: list[str], fc, label: str) -> str:
    """Umweg-/Rueckwaerts-Check fuer EINE Teilstrecke (siehe evaluate() Regel
    3/4). Muss pro Leg aufgerufen werden, NIE mit off.route (das haengt bei
    multi_city Hin+Rueck aneinander - route[0]/route[-1] waeren dann Start
    und Rueckkehr-Heimatflughafen, z.B. STR->MUC ~190km, weit unter
    detour_min_direct_km -> der Check wuerde fuer JEDES Multi-City-Angebot
    stillschweigend uebersprungen, verifiziert 11.09.26 fuer alle 6
    Flughafen-Paare STR/MUC/FRA/ZRH)."""
    if len(route) < 2:
        return ""
    a0, aZ = get_airport(route[0]), get_airport(route[-1])
    direct_km = haversine_km(a0, aZ)
    if direct_km < fc.detour_min_direct_km:
        return ""
    route_km = route_distance_km(route)
    if route_km > fc.max_detour_ratio * direct_km:
        return f"umweg_{label} ({route_km / direct_km:.2f}x direkt)"
    for code in layovers:
        hub_to_dest = haversine_km(get_airport(code), aZ)
        if hub_to_dest > direct_km * 1.20:
            return f"rueckwaerts_routing_{label} ({code} weiter weg als Start)"
    return ""


def evaluate(off: FlightOffer, cfg: Config) -> None:
    fc = cfg.flight_constraints

    if not off.segments or off.price_total <= 0:
        _mark(off, "unvollstaendig")
        return

    # (-1) Gruppen-Check widerlegt: eine echte Suche mit der vollen
    # Personenzahl hat diese 1-Pax-Hochrechnung nicht bestaetigt (siehe
    # sources/flights.py collect_flights, group_check).
    if off.group_check_unconfirmed:
        _mark(off, off.group_check_unconfirmed)
        return

    # (0) Ausgeschlossene Airlines
    blocked = list(fc.excluded_airlines)
    if fc.exclude_gulf_carriers:
        blocked += GULF_CARRIERS
    if blocked:
        low_airlines = [a.lower() for a in off.airlines]
        for name in blocked:
            n = name.lower()
            if any(n in a or a in n for a in low_airlines):
                _mark(off, f"airline_ausgeschlossen ({name})")
                return

    # (2) Stopps PRO RICHTUNG (bei multi_city Hin- und Rueckstrecke einzeln
    #     pruefen - die Summe waere hier der falsche Massstab).
    out_stops = FlightOffer._leg_stops(off.segments)
    ret_stops = FlightOffer._leg_stops(off.return_segments)
    if out_stops > fc.max_stops_per_direction or ret_stops > fc.max_stops_per_direction:
        _mark(off, f"zu_viele_stopps (hin={out_stops}, rueck={ret_stops})")
        return

    # (1) Abreisetag-Zeitfenster: nur fuer den ECHTEN Start ab einem der
    #     Heimatflughaefen (STR/MUC/FRA/ZRH), nur erster Leg am Starttag.
    #     Airport-spezifisch (siehe earliest_departure_for) - STR hat eine
    #     andere fruehste Abflugzeit als MUC/FRA/ZRH (13.09.26 Nutzervorgabe).
    if (off.direction in ("outbound", "round_trip", "multi_city")
            and off.origin in set(cfg.trip.origin_airports)):
        first = off.segments[0]
        if first.departure.date() == off.search_date:
            limit = fc.earliest_departure_for(first.from_airport)
            if first.departure.timetz().replace(tzinfo=None) < limit:
                _mark(off, f"abflug_vor_{limit.strftime('%H:%M')}")
                return

    # (6) Layover-Sanity - NICHT fuer Angebote mit nur geschaetzten Segment-
    # Zeiten (siehe FlightOffer.segment_times_approximate): die einzelnen
    # Umstiegszeiten sind dort gleichmaessig auf die Gesamtreisezeit verteilt,
    # kein echtes Timing - ein Check darauf wuerde nur Zufallswerte pruefen.
    # BKK->USM (kurzer Zubringer innerhalb Thailands) braucht weniger
    # Mindest-Umstiegszeit als ein internationaler Umstieg (13.09.26
    # Nutzervorgabe) - siehe _min_layover_for().
    if not off.segment_times_approximate:
        for segs in (off.segments, off.return_segments):
            _, minutes = FlightOffer._leg_layovers(segs)
            for i, mins in enumerate(minutes):
                threshold = _min_layover_for(segs[i].to_airport, segs[i + 1].to_airport, fc)
                if mins < threshold:
                    _mark(off, f"umstieg_zu_knapp ({mins} min, Mindest {threshold} min)")
                    return
                if mins > fc.max_layover_hours * 60:
                    _mark(off, f"layover_zu_lang ({mins // 60} h)")
                    return

    # (5) Reisezeit PRO RICHTUNG (nicht die Summe - sonst waeren Multi-City-
    #     Tickets mit zwei soliden ~20h-Strecken faelschlich ausgeschlossen)
    out_dur = FlightOffer._leg_duration(off.segments)
    ret_dur = FlightOffer._leg_duration(off.return_segments)
    limit_min = fc.max_total_travel_hours * 60
    if out_dur > limit_min or ret_dur > limit_min:
        _mark(off, f"reisezeit_zu_lang (hin={out_dur // 60}h, rueck={ret_dur // 60}h)")
        return

    # (3)/(4) Geometrie PRO RICHTUNG (bei multi_city Hin- und Ruecklegs
    # einzeln - siehe _geometry_violation-Docstring fuer den Bug, den das
    # ersetzt: off.route[0]/[-1] waeren bei multi_city Start/Rueckkehr-
    # Heimatflughafen, nicht Start/USM, und haetten den Check fuer JEDES
    # Multi-City-Angebot unbemerkt deaktiviert).
    out_layovers, _ = FlightOffer._leg_layovers(off.segments)
    violation = _geometry_violation(_leg_route(off.segments), out_layovers, fc, "hin")
    if not violation and off.return_segments:
        ret_layovers, _ = FlightOffer._leg_layovers(off.return_segments)
        violation = _geometry_violation(_leg_route(off.return_segments), ret_layovers, fc, "rueck")
    if violation:
        _mark(off, violation)
        return


def apply_constraints(offers: list[FlightOffer], cfg: Config) -> dict:
    """Alle Angebote bewerten. Returns Zusammenfassung fuer die Logs/API."""
    reasons: dict[str, int] = {}
    for off in offers:
        off.excluded = False
        off.exclude_reason = ""
        evaluate(off, cfg)
        if off.excluded:
            key = off.exclude_reason.split(" ")[0]
            reasons[key] = reasons.get(key, 0) + 1

    kept = sum(1 for o in offers if not o.excluded)
    summary = {"total": len(offers), "kept": kept,
               "excluded": len(offers) - kept, "reasons": reasons}
    log.info("Constraints: %d/%d behalten, ausgefiltert=%s",
             kept, len(offers), reasons)
    return summary
