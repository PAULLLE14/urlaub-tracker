"""Normalisierte Angebots-Objekte, die alle Quellen zurueckliefern.

Die Quellen (Flug/Hotel/Pauschal) sind bewusst voneinander entkoppelt: jede
liefert eine Liste dieser Dataclasses, die Vergleichslogik und Persistenz
kennen nur diese Formate - nicht die Interna der jeweiligen Quelle.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Flug
# --------------------------------------------------------------------------- #
@dataclass
class Segment:
    from_airport: str
    to_airport: str
    departure: datetime          # tz-aware, Lokalzeit Abflughafen
    arrival: datetime            # tz-aware, Lokalzeit Ankunftflughafen
    departure_tz: str
    arrival_tz: str
    duration_minutes: int
    airline: str = ""
    plane_type: str = ""

    def as_dict(self) -> dict:
        return {
            "from": self.from_airport,
            "to": self.to_airport,
            "departure": self.departure.isoformat(),
            "arrival": self.arrival.isoformat(),
            "departure_tz": self.departure_tz,
            "arrival_tz": self.arrival_tz,
            "duration_minutes": self.duration_minutes,
            "airline": self.airline,
            "plane_type": self.plane_type,
        }


@dataclass
class FlightOffer:
    """Ein Angebot ist IMMER eine zusammenhaengende Buchung (ein Ticket):
    ``round_trip`` (gleicher Flughafen Hin/Rueck) oder ``multi_city``
    (Hinflug-Airport != Rueckflug-Airport, trotzdem ein Ticket, geschuetzte
    Anschluesse). Reine Einzelrichtungs-Angebote (``one_way``) werden von den
    Quellen zwar weiterhin optional erzeugt (Debug/Referenz), aber von
    Vergleichslogik und Dashboard nicht mehr als Buchungsoption behandelt.
    """

    source: str
    direction: str                # "outbound" | "return" | "round_trip" | "multi_city"
    origin: str                   # tatsaechlicher Start-IATA dieser Option
    destination: str              # tatsaechlicher End-IATA (bei multi_city = Rueckflug-Airport)
    search_date: date             # angefragtes Abflugdatum (Hinflug)
    price_total: float            # fuer alle Pax (nach Normalisierung)
    price_per_person: float
    currency: str
    airlines: list[str] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)       # Hinflug-Strecke
    return_segments: list[Segment] = field(default_factory=list)  # nur multi_city
    deep_link: str = ""
    captured_at: datetime = field(default_factory=utcnow)
    carbon_grams: int | None = None
    # "one_way" | "round_trip" | "multi_city". Bei round_trip ist price_total
    # der Gesamtpreis fuer Hin+Rueck (ein Ticket); Google liefert dabei nur die
    # Hinflug-Strecke im Detail. Bei multi_city liefert Google BEIDE Strecken.
    trip_type: str = "one_way"
    return_date: date | None = None

    # von der Filterlogik gesetzt
    excluded: bool = False
    exclude_reason: str = ""

    # "estimated" (1-Pax-Suche x Personenzahl) oder "group" (echte Suche mit
    # der vollen Personenzahl - siehe sources/flights.py collect_flights).
    pax_mode: str = "estimated"
    # Nur fuer pax_mode="split_4_4" gesetzt (siehe flights_group_browser.py
    # 14.09.26 Umbau, externe Review Punkt A2): eine 4+4-Aufteilung ist NIE
    # ein bestaetigter Preis, sondern entweder eine reine Preis-UNTERGRENZE
    # (beide 4er-Suchen koennten denselben knappen Tarif-Bucket treffen, der
    # in Wirklichkeit nur fuer EINE der beiden Buchungen reicht) oder eine
    # konservative Schaetzung (2. Familie zum vollen 8-Pax-Preis kalkuliert).
    # Leerstring fuer alle anderen pax_mode - dort ist der Preis entweder
    # eine echte Hochrechnung (estimated) oder ein real bestaetigter
    # Gesamtpreis (group).
    price_confidence: str = ""
    # Von collect_flights gesetzt, wenn eine 1-Pax-Hochrechnung durch eine
    # echte Gruppen-Suche widerlegt wurde (die Gruppe bekommt diesen Preis
    # nachweislich NICHT). route_filter.evaluate() liest das und schliesst
    # aus - als eigenes Feld statt direkt "excluded", weil apply_constraints
    # excluded/exclude_reason bei jedem Lauf zurücksetzt und neu bewertet.
    group_check_unconfirmed: str = ""

    # True fuer Multi-City-Angebote aus flights_multicity_browser.py: Preis/
    # Airlines/Stopp-Zahl/Layover-FLUGHAFEN sind echt aus dem gerenderten
    # Google-Flights-Text, aber die einzelnen Segment-ZEITEN dazwischen sind
    # nicht sichtbar und werden gleichmaessig auf die Gesamtreisezeit
    # verteilt (kein echtes Timing je Teilstrecke). route_filter.py
    # ueberspringt deshalb fuer solche Angebote den minutengenauen
    # Layover-Check (der wuerde auf den synthetischen Zeiten nur Muell
    # pruefen), Stopp-Zahl/Zeitfenster/Geometrie nutzen echte Daten weiter.
    segment_times_approximate: bool = False

    # Roadmap Runde 2, Punkt 2.1 ("Preis-Leiter"): macht fuer die Top-5-
    # Kombinationen sichtbar, WARUM der Preis hier hoeher ist als eine
    # schnelle manuelle 1-Pax-Suche ohne Gepaeck - Keys je nach Verfuegbarkeit
    # "1_pax_ohne_gepaeck", "1_pax_mit_gepaeck", "4_pax", "8_pax" (EUR p.P.).
    # Nur auf dem "group"-Angebot (echter 8-Pax-Preis) gesetzt, leer sonst.
    price_ladder: dict = field(default_factory=dict)

    # ---- abgeleitete Werte (kombinieren Hin- + ggf. Rueckstrecke) ---------
    @staticmethod
    def _leg_stops(segs: list[Segment]) -> int:
        return max(0, len(segs) - 1)

    @staticmethod
    def _leg_layovers(segs: list[Segment]) -> tuple[list[str], list[int]]:
        airports, minutes = [], []
        for i in range(len(segs) - 1):
            airports.append(segs[i].to_airport)
            gap = (segs[i + 1].departure - segs[i].arrival).total_seconds()
            minutes.append(int(round(gap / 60)))
        return airports, minutes

    @staticmethod
    def _leg_duration(segs: list[Segment]) -> int:
        if not segs:
            return 0
        return int(round((segs[-1].arrival - segs[0].departure).total_seconds() / 60))

    @property
    def stops(self) -> int:
        """Stopps insgesamt (Hin + Rueck bei multi_city), OHNE den ~2-Wochen-
        'Aufenthalt' zwischen den Strecken als Stopp zu zaehlen."""
        return self._leg_stops(self.segments) + self._leg_stops(self.return_segments)

    @property
    def layover_airports(self) -> list[str]:
        a1, _ = self._leg_layovers(self.segments)
        a2, _ = self._leg_layovers(self.return_segments)
        return a1 + a2

    @property
    def layover_minutes(self) -> list[int]:
        _, m1 = self._leg_layovers(self.segments)
        _, m2 = self._leg_layovers(self.return_segments)
        return m1 + m2

    @property
    def total_duration_minutes(self) -> int:
        """Reine Flugzeit-Spanne je Strecke summiert - NICHT die Zeit vor Ort."""
        return self._leg_duration(self.segments) + self._leg_duration(self.return_segments)

    @property
    def route(self) -> list[str]:
        if not self.segments:
            return [self.origin, self.destination]
        codes = [self.segments[0].from_airport] + [s.to_airport for s in self.segments]
        if self.return_segments:
            codes += [s.to_airport for s in self.return_segments]
        return codes

    @property
    def return_route(self) -> list[str]:
        if not self.return_segments:
            return []
        return [self.return_segments[0].from_airport] + [s.to_airport for s in self.return_segments]

    @property
    def dedup_key(self) -> tuple:
        # pax_mode gehoert bewusst dazu: eine "estimated" (1-Pax-Hochrechnung)
        # und eine "group" (echte Gruppensuche) Zeile fuer denselben Flug
        # (gleiche Route/Zeit/Airline) sind NICHT dasselbe Angebot - die eine
        # kann per gruppen_check ausgeschlossen sein, die andere der einzige
        # bestaetigte echte Preis. Ohne pax_mode im Key wuerde dedup_flights()
        # (siehe logic/normalize.py) sonst die guenstigere (oft die gerade
        # widerlegte) Zeile behalten und die bestaetigte Gruppen-Zeile
        # stillschweigend verwerfen - Bugfix 11.09.26.
        return (
            self.trip_type,
            tuple(self.route),
            self.segments[0].departure.isoformat() if self.segments else str(self.search_date),
            str(self.return_date),
            tuple(self.airlines),
            self.pax_mode,
        )


# --------------------------------------------------------------------------- #
# Hotel
# --------------------------------------------------------------------------- #
@dataclass
class HotelOffer:
    source: str
    ok: bool
    price_total: float | None            # 3 Zimmer x 14 Naechte, alle Gaeste
    currency: str
    room_desc: str = ""
    nights: int = 0
    rooms: int = 0
    guests: int = 0
    per_night: float | None = None
    deep_link: str = ""
    captured_at: datetime = field(default_factory=utcnow)
    error: str = ""
    raw: dict = field(default_factory=dict)
    is_reference: bool = False           # aus config.trip.reference_offers


# --------------------------------------------------------------------------- #
# Pauschalreise
# --------------------------------------------------------------------------- #
@dataclass
class PackageOffer:
    source: str
    ok: bool
    price_total: float | None            # gesamtes Paket, alle Pax
    currency: str
    operator: str = ""
    hotel_name: str = ""
    dep_airport: str = ""
    board: str = ""
    nights: int = 0
    persons: int = 0
    deep_link: str = ""
    captured_at: datetime = field(default_factory=utcnow)
    error: str = ""
    raw: dict = field(default_factory=dict)
    is_reference: bool = False           # aus config.trip.reference_offers
