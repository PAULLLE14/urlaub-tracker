"""Geteilte Karten-Parsing-Logik fuer Playwright-basierte Google-Flights-Scrapes
(Multi-City UND Gruppen-/Split-Check).

Beide Quellen rendern strukturell identische Ergebnis-"Karten" (Abflugzeit,
Ankunftszeit, Airlines, Dauer, Route, Stopps, Layover-Flughaefen, Preis) -
nur der Text NACH dem Preis unterscheidet sich ("gesamte Reise" bei
Multi-City, "Hin und zurück" bei Round-Trip). Diese eine Implementierung wird
von beiden genutzt, damit ein Bugfix (z.B. Layover-Zaehler-Pruefung) nicht an
zwei Stellen gepflegt werden muss.

Bugfix 14.09.26 (externe Code-Review, Punkt A1): der Gruppen-Check in
``flights_group_browser.py`` las frueher NUR den Preis aus der guenstigsten
8-Pax-Karte, das resultierende Angebot bekam aber Airlines/Segmente vom
1-Pax-Schaetzangebot geliehen - bei einem anderen guenstigsten Flug fuer 8
Personen (z.B. Condor statt Qatar) zeigte das Dashboard einen falschen
Airline/Zeiten-Preis-Mix, UND ``apply_constraints()`` prueft dadurch die
FALSCHEN Segmentdaten (ein Austrian-Flug oder Abflug vor 16:30 haette so
durchrutschen koennen). Mit ``parse_cards()``/``build_approx_segments()``
bekommt JEDES Angebot (Multi-City wie Gruppen-Check) seine EIGENEN, zur
Karte passenden Segmentdaten.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ..geo import get_airport
from ..offers import Segment
from .scraper_base import parse_money

_AIRPORT_CODE = re.compile(r"\b[A-Z]{3}\b")

# Plausibilitaets-Fenster fuer einen p.P.-Preis auf der Route STR/MUC/FRA/ZRH
# <-> USM (Roadmap Runde 2, Punkt 1.1): CheckRun #17 zeigte eine Multi-City-
# Karte mit 7.391 EUR p.P. - deutlich ausserhalb dessen, was fuer diese Route
# real vorkommt (auch der als real verifizierte 58.913-EUR-Fall lag bei
# ~7.364 p.P. und war ein Mixed-Cabin-Sonderfall, kein Normalpreis). Ausserhalb
# dieses Fensters ist die Karte eher ein Parse-Fehler (falscher Preis-Kontext)
# als ein echtes Angebot - wird deshalb NIE als bestaetigt gefuehrt, bleibt
# aber (ueber group_check_unconfirmed) im Dashboard sichtbar/nachvollziehbar,
# nicht geloescht.
PLAUSIBLE_PP_MIN = 400
PLAUSIBLE_PP_MAX = 3000


# Roadmap Runde 2/3, Punkt 2.3/6.4: Google zeigt im "Preise beobachten"-
# Baustein einer Ergebnisliste die tatsaechlich gesuchten Daten NOCHMAL in
# maschinenlesbarer Form, z.B. "... mit Abflug am 2027-05-14 und Ankunft am
# 2027-05-28 Preise beobachten" - live verifiziert (15.09.26) auf einer
# echten FRA-USM-Round-Trip-Suche. Zuverlässiger als das "14.–28. Mai 2027"-
# Datum daneben (das ist lokalisiert und schwerer robust zu parsen). Damit
# laesst sich hart pruefen, ob eine Karte wirklich zu den angefragten Daten
# gehoert, statt blind zu vertrauen, dass die URL-Parameter gegriffen haben.
_DATE_CONFIRM = re.compile(r"Abflug am (\d{4}-\d{2}-\d{2}) und Ankunft am (\d{4}-\d{2}-\d{2})")


def confirmed_dates(body: str) -> tuple[str, str] | None:
    """(Abflugdatum, Ankunftsdatum) laut Google's eigenem "Preise beobachten"-
    Text, oder None wenn dieser Baustein nicht im Text vorkommt (z.B. weil
    die Seite anders gerendert wurde - dann keine Aussage moeglich, NICHT als
    Fehler werten)."""
    m = _DATE_CONFIRM.search(body)
    return (m.group(1), m.group(2)) if m else None


def implausible_price_reason(price_per_person: float) -> str:
    """Leerstring wenn plausibel, sonst ein Grund fuer
    ``FlightOffer.group_check_unconfirmed`` (siehe Docstring oben)."""
    if PLAUSIBLE_PP_MIN <= price_per_person <= PLAUSIBLE_PP_MAX:
        return ""
    return (f"parse_suspect (p.P. {price_per_person:.0f} EUR ausserhalb "
            f"{PLAUSIBLE_PP_MIN}-{PLAUSIBLE_PP_MAX} EUR - vermutlich falsche "
            "Karte/Preis-Kontext geparst, manuell pruefen)")


def card_pattern(end_marker: str) -> re.Pattern:
    """``end_marker`` ist der Text direkt nach dem Preis, der eine Karte
    abschliesst - "gesamte Reise" (Multi-City) oder "Hin und zurück"
    (Round-Trip)."""
    return re.compile(
        r"(?P<dep>\d{1,2}:\d{2})\s*\n\s*–\s*\n\s*"
        r"(?P<arr>\d{1,2}:\d{2})(?:\+(?P<arr_days>\d+))?\s*\n\s*"
        r"(?P<airlines>[^\n]+?)\s*\n\s*"
        r"(?P<duration>(?:\d+\s*Std\.?\s*)?(?:\d+\s*Min\.?)?)\s*\n\s*"
        r"(?P<origin>[A-Z]{3})–(?P<dest>[A-Z]{3})\s*\n\s*"
        r"(?P<stops>Nonstop|Direkt|\d+\s*Stopps?)\s*\n\s*"
        r"(?:(?P<layover_line>[^\n]*[A-Z]{3}[^\n]*)\s*\n\s*)?"
        rf".*?(?P<price>[\d.,]+)\s*€\s*\n\s*{re.escape(end_marker)}",
        re.S,
    )


def _duration_minutes(text: str) -> int:
    h = re.search(r"(\d+)\s*Std", text)
    m = re.search(r"(\d+)\s*Min", text)
    return (int(h.group(1)) * 60 if h else 0) + (int(m.group(1)) if m else 0)


def _stops_count(text: str) -> int:
    if text.strip() in ("Nonstop", "Direkt"):
        return 0
    m = re.search(r"(\d+)", text)
    return int(m.group(1)) if m else 0


def parse_cards(body: str, end_marker: str) -> list[dict]:
    """Alle Ergebniskarten mit Preis + kompletten Flugdaten (nicht nur den
    Preis - siehe Modul-Docstring)."""
    out = []
    for m in card_pattern(end_marker).finditer(body):
        price = parse_money(m.group("price"))
        if not price:
            continue
        stops = _stops_count(m.group("stops"))
        layovers = _AIRPORT_CODE.findall(m.group("layover_line") or "")
        if len(layovers) != stops:
            # Karte nicht sauber geparst (Layover-Zahl passt nicht zur
            # Stopp-Zahl) - lieber ueberspringen als falsche Airports uebernehmen.
            continue
        out.append({
            "dep_time": m.group("dep"),
            "airlines": [a.strip() for a in m.group("airlines").split(",")],
            "duration_min": _duration_minutes(m.group("duration")),
            "origin": m.group("origin"), "dest": m.group("dest"),
            "stops": stops, "layovers": layovers, "price": price,
        })
    return out


def cheapest_card(cards: list[dict]) -> dict | None:
    return min(cards, key=lambda c: c["price"]) if cards else None


def build_approx_segments(origin: str, dest: str, layovers: list[str], dep_time: str,
                          total_minutes: int, day: date) -> list[Segment]:
    """Baut ``len(layovers)+1`` Segmente mit ECHTEN Flughaefen, aber
    gleichmaessig verteilten (geschaetzten) Einzel-Zeiten - die Karte zeigt
    nur Gesamt-Abflug/Ankunft/Dauer, keine Zwischenzeiten. Aufrufer MUSS
    ``FlightOffer.segment_times_approximate = True`` setzen."""
    airports = [origin, *layovers, dest]
    n = len(airports) - 1
    per_leg = max(1, total_minutes // n)
    o0 = get_airport(origin)
    # "cur" ist immer der ABSOLUTE Zeitpunkt (Instant) - wird pro Etappe in
    # die Zeitzone des jeweiligen Flughafens konvertiert (sonst haette z.B.
    # eine BKK-Ankunft faelschlich die Abflug-Zeitzone gezeigt).
    cur = datetime.fromisoformat(f"{day.isoformat()}T{dep_time}:00").replace(tzinfo=ZoneInfo(o0.tz))
    segs = []
    for i in range(n):
        frm, to = airports[i], airports[i + 1]
        dtz, atz = get_airport(frm).tz, get_airport(to).tz
        dep = cur.astimezone(ZoneInfo(dtz))
        arr_instant = dep + timedelta(minutes=per_leg)
        arr = arr_instant.astimezone(ZoneInfo(atz))
        segs.append(Segment(from_airport=frm, to_airport=to, departure=dep, arrival=arr,
                            departure_tz=dtz, arrival_tz=atz, duration_minutes=per_leg))
        cur = arr_instant
    return segs
