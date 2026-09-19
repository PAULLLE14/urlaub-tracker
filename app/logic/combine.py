"""Vergleichslogik: guenstigste Gesamtkombination vs. Pauschalreise.

Arbeitet auf den normalisierten Dataclasses (vor DB-Persistenz), damit die
Logik isoliert testbar bleibt. Ergebnis ist ein :class:`Verdict` mit den
konkret gewaehlten Angeboten und allen Kennzahlen fuers Dashboard.

Bei Fluegen zaehlen NUR zusammenhaengende Buchungen (ein Ticket): Round-Trip
(gleicher Flughafen) oder Multi-City (unterschiedliche Hin-/Rueckflughaefen).
Reine Einzelrichtungs-Angebote fliessen nicht in den Vergleich ein.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from ..config import Config
from ..logging_setup import get_logger
from ..offers import FlightOffer, HotelOffer, PackageOffer

log = get_logger("logic.combine")

_CONNECTED_TYPES = ("round_trip", "multi_city")


@dataclass
class Verdict:
    currency: str = "EUR"
    flight: FlightOffer | None = None     # guenstigste zusammenhaengende Buchung
    flight_total: float | None = None
    # Nur informativ: bei Round-Trip zeigt Google selbst nur den Hinflug im
    # Detail. Referenz-Rueckflug (Einzelrichtungs-Suche, selber Zeitraum/
    # Flughafen) zeigt ein plausibles Routing-Beispiel - NICHT Teil des
    # Round-Trip-Preises und keine eigene Buchungsoption.
    return_reference: FlightOffer | None = None
    hotel: HotelOffer | None = None
    hotel_total: float | None = None
    hotel_checkin: str | None = None
    hotel_checkout: str | None = None
    hotel_exact: bool = False          # True: Hotel live fuer genau die Flugdaten abgefragt
    separate_total: float | None = None
    package: PackageOffer | None = None
    package_total: float | None = None
    winner: str = "unbekannt"          # "einzelbuchung" | "pauschalreise" | "unbekannt"
    delta: float | None = None          # package_total - separate_total
    notes: list[str] = field(default_factory=list)
    # Echte Naechte-Zahl des GEWAEHLTEN Flugs (cfg.trip.nights + Delta aus
    # _hotel_nights_delta), nicht der feste Konfigurationswert - Roadmap
    # Runde 2, Punkt 1.6: die Dashboard-Kopfzeile zeigte bisher immer die
    # statische Config-Zahl, auch wenn der gewaehlte Flug tatsaechlich eine
    # andere Naechte-Zahl braucht.
    nights_used: int | None = None

    def as_dict(self) -> dict:
        # Bugfix 13.09.26 (Nutzer: "die Preise werden mir garnicht gezeigt,
        # kannst du mir da Links mitgeben"): die Kopf-Zusammenfassung
        # ("Einzelbuchung vs. Pauschalreise") zeigte bisher NUR Zahlen ohne
        # jeden Link, weil hotel/package hier gar nicht mit ausgegeben
        # wurden (nur "hotel_total: 6909", keine Quelle/kein deep_link) -
        # man musste erst in die Detailtabellen weiter unten scrollen, um
        # ueberhaupt einen klickbaren Link zu den einzelnen Zimmer-Preisen
        # zu finden. Jetzt tragen flight/hotel/package ihre eigenen
        # deep_link+Quelle direkt in der Zusammenfassung mit.
        f, r, h, p = self.flight, self.return_reference, self.hotel, self.package
        return {
            "currency": self.currency,
            "flight_source": self.flight.trip_type if f else None,
            "flight_total": self.flight_total,
            "hotel_total": self.hotel_total,
            "separate_total": self.separate_total,
            "package_total": self.package_total,
            "winner": self.winner,
            "delta": self.delta,
            "notes": self.notes,
            "nights_used": self.nights_used,
            "flight": None if not f else {
                "trip_type": f.trip_type,
                "price_total": f.price_total, "price_per_person": f.price_per_person,
                "route": f.route, "return_route": f.return_route,
                "airlines": f.airlines,
                "search_date": f.search_date.isoformat(),
                "return_date": f.return_date.isoformat() if f.return_date else None,
                "deep_link": f.deep_link,
                "pax_mode": f.pax_mode, "price_confidence": f.price_confidence,
                "segment_times_approximate": f.segment_times_approximate,
                "price_ladder": f.price_ladder,
                "booking_options": f.booking_options,
            },
            "return_reference": None if not r else {
                "route": r.route, "airlines": r.airlines,
                "price_per_person": r.price_per_person,
                "total_duration_minutes": r.total_duration_minutes,
                "stops": r.stops, "search_date": r.search_date.isoformat(),
                "deep_link": r.deep_link,
            },
            "hotel": None if not h else {
                "source": h.source, "price_total": h.price_total,
                "per_night": h.per_night, "deep_link": h.deep_link,
                "is_reference": h.is_reference,
                "checkin": self.hotel_checkin, "checkout": self.hotel_checkout,
                "date_exact": self.hotel_exact,
                "per_room_size": (h.raw or {}).get("per_room_size"),
                "room_split": (h.raw or {}).get("room_split"),
            },
            "package": None if not p else {
                "operator": p.operator, "price_total": p.price_total,
                "deep_link": p.deep_link, "is_reference": p.is_reference,
            },
        }


def _hotel_nights_delta(flight: FlightOffer, cfg: Config) -> int | None:
    """Wie viele Naechte MEHR (oder weniger) als die konfigurierten
    cfg.trip.nights dieser Flug fuers Hotel bedeuten wuerde, abgeleitet aus
    seinem echten Abflug- UND Rueckflugdatum. None wenn kein Rueckflugdatum
    bekannt (sollte fuer round_trip/multi_city nie passieren).

    Der tatsaechliche Hotel-Checkin ist NICHT einfach das feste
    cfg.trip.hotel_checkin, sondern immer Abflugdatum + 1 Tag (Nachtflug,
    Ankunft in Thailand erst am Folgetag - das ist ein physikalisches
    Faktum der Flugzeit/Zeitverschiebung, das fuer JEDES gesuchte
    Abflugdatum gilt, nicht nur das konfigurierte). Sonst wuerde bei
    mehreren moeglichen Abflugtagen (z.B. 14. ODER 15.05., Nutzerwunsch
    12.09.26) ein Flug am 15. faelschlich mit dem fuer den 14. berechneten
    Hotel-Checkin verglichen - waere dann eine Nacht "zu kurz" gerechnet.
    Normalerweise 0 (Standard-Kombination), aber bei einem alternativ
    gesuchten Datumspaar braucht das Hotel eine andere Naechte-Zahl als der
    fest hinterlegte Hotelpreis einpreist - sonst wuerde ein nur scheinbar
    guenstigerer Flug den echten Gesamtpreis verfaelschen (Bugfix 12.09.26:
    _pick_flight waehlte vorher rein nach Flugpreis, ignorierte dass ein
    anderes Datumspaar eine andere Hotel-Naechte-Zahl braucht)."""
    if flight.return_date is None:
        return None
    actual_checkin = flight.search_date + timedelta(days=1)
    return (flight.return_date - actual_checkin).days - cfg.trip.nights


def _hotel_tier(h: HotelOffer) -> int:
    """Konkreter Scrape (0) schlaegt manuelle Referenz (1) schlaegt Floor-/1-Nacht-Richtwert (2)."""
    raw = h.raw or {}
    is_floor = raw.get("estimate") or "Floor" in raw.get("basis", "")
    if getattr(h, "is_reference", False):
        return 1
    return 2 if is_floor else 0


def _best_hotel(pool: list[HotelOffer]) -> HotelOffer | None:
    if not pool:
        return None
    best_tier = min(_hotel_tier(h) for h in pool)
    return min((h for h in pool if _hotel_tier(h) == best_tier), key=lambda h: h.price_total)


def _hotel_dates(h: HotelOffer, cfg: Config) -> tuple[str, str]:
    """Checkin/Checkout, fuer die dieses Angebot gilt (ungetaggte = Config-Standard)."""
    raw = h.raw or {}
    return (raw.get("checkin") or cfg.trip.hotel_checkin.isoformat(),
            raw.get("checkout") or cfg.trip.hotel_checkout.isoformat())


def _hotel_for_flight(flight: FlightOffer, hotels: list[HotelOffer],
                      cfg: Config) -> tuple[HotelOffer | None, float | None, bool]:
    """Passendes Hotel zu genau DIESEM Flug: Checkin = Abflug + 1 Tag,
    Checkout = Rueckflugdatum. Gibt (Hotel, Preis, exakt) zurueck. Exakt heisst:
    fuer genau diese Daten live abgefragt. Sonst (nur Fallback, z.B. Quelle
    ausgefallen): Standardzeitraum-Angebot linear auf die Naechte umgerechnet."""
    if not hotels:
        return None, None, False
    if flight.return_date is not None:
        target = ((flight.search_date + timedelta(days=1)).isoformat(),
                  flight.return_date.isoformat())
        exact = _best_hotel([h for h in hotels if _hotel_dates(h, cfg) == target])
        if exact:
            return exact, exact.price_total, True
    default = (cfg.trip.hotel_checkin.isoformat(), cfg.trip.hotel_checkout.isoformat())
    base = _best_hotel([h for h in hotels if _hotel_dates(h, cfg) == default]) or _best_hotel(hotels)
    price = base.price_total
    delta = _hotel_nights_delta(flight, cfg)
    if delta and base.per_night:
        price += delta * base.per_night
    return base, price, False


def _true_total(flight: FlightOffer, hotels: list[HotelOffer], cfg: Config) -> float:
    """Flugpreis + der zu SEINEM Datumspaar passende Hotelpreis - die Zahl, nach
    der die guenstigste Gesamtkombination gewaehlt wird, nicht der nackte Flugpreis."""
    _, price, _ = _hotel_for_flight(flight, hotels, cfg)
    return flight.price_total + (price or 0)


def _drop_superseded_estimates(offers: list[FlightOffer]) -> list[FlightOffer]:
    """Bugfix 13.09.26 (Nutzer: "sieht aus wie eine 1-Pax-Suche" - die
    Kopf-Zusammenfassung wollte die guenstigste Option, waehlte dabei aber
    oft eine unbestaetigte 1-Pax-Hochrechnung, OBWOHL fuer dieselbe Route/
    Termin-Kombination direkt daneben ein ECHT gepruefter Gruppen-/Split-
    Preis vorlag - group_check flaggt eine Hochrechnung nur dann als
    widerlegt (group_check_unconfirmed), wenn der echte Preis HOEHER war;
    war er gleich oder niedriger, blieb die Hochrechnung unmarkiert im
    Kandidaten-Pool und konnte trotzdem gewinnen, obwohl ihr eigener Preis
    nie verifiziert wurde. Sobald fuer eine Route/Termin-Kombination ein
    echter (group/split_*) Preis existiert, ist die Hochrechnung fuer genau
    diese Kombination nur noch Rohdatum, kein verlaesslicher Kandidat mehr -
    gleiche Logik wie frontend/app.js filterEstimates()."""
    verified_keys = {
        (o.trip_type, o.origin, o.destination, o.search_date, o.return_date)
        for o in offers if o.pax_mode == "group" or o.pax_mode.startswith("split_")
    }
    return [o for o in offers if not (
        o.pax_mode == "estimated"
        and (o.trip_type, o.origin, o.destination, o.search_date, o.return_date) in verified_keys
    )]


def _pick_flight(flights: list[FlightOffer], hotel: list[HotelOffer],
                 cfg: Config) -> tuple[FlightOffer | None, list[str]]:
    notes: list[str] = []
    connected = [o for o in flights
                if o.trip_type in _CONNECTED_TYPES and not o.excluded and o.price_total > 0]
    connected = _drop_superseded_estimates(connected)
    if not connected:
        notes.append("Noch keine zusammenhaengende Buchung (Round-Trip/Multi-City) "
                     "gefunden - Einzelrichtungs-Tickets werden nicht angezeigt.")
        return None, notes

    # Nicht einfach den billigsten FLUG nehmen: ein anderes Abflug- oder
    # Rueckflugdatum als die konfigurierte Standard-Kombination braucht eine
    # andere Anzahl Hotel-Naechte - erst der GESAMTPREIS (Flug + dazu
    # passendes Hotel) entscheidet, welche Kombination wirklich am
    # guenstigsten ist.
    best = min(connected, key=lambda o: _true_total(o, hotel, cfg))

    # Nutzer-Fund 16.09.26: eine split_4_4-Schaetzung ist IMMER nur eine
    # konservative OBERGRENZE (siehe offers.py price_confidence), kein
    # bestaetigter Preis - trotzdem konnte sie bisher schon bei einem
    # knappen rechnerischen Vorsprung einen echt GEPRUEFTEN Preis (pax_mode
    # "group") als Kopf-Zahl verdraengen. Das widerspricht "keine
    # Schaetzungen, nur harte Pruefungen": ein Vorsprung von nur 1-2% ist bei
    # einer Obergrenzen-Schaetzung kein verlaesslicher Vorteil. Nur wenn die
    # Schaetzung SPUERBAR (>3%) guenstiger bleibt, darf sie gewinnen.
    _CONFIRMED_PREFERENCE_MARGIN = 0.97  # Schaetzung muss <=97% des bestaetigten Preises sein
    if best.pax_mode != "group":
        confirmed_opts = [o for o in connected if o.pax_mode == "group"]
        if confirmed_opts:
            best_confirmed = min(confirmed_opts, key=lambda o: _true_total(o, hotel, cfg))
            if _true_total(best, hotel, cfg) >= _true_total(best_confirmed, hotel, cfg) * _CONFIRMED_PREFERENCE_MARGIN:
                notes.append(
                    f"Eine Schaetzung ({best.price_total:.0f} EUR, {best.pax_mode}, "
                    f"{best.search_date} -> {best.return_date}) war rechnerisch knapp "
                    f"guenstiger, ist aber nur eine Obergrenzen-Schaetzung, kein "
                    f"bestaetigter Preis - bei einem Abstand unter 3% gewinnt der echt "
                    f"geprueften Preis ({best_confirmed.price_total:.0f} EUR, "
                    f"{best_confirmed.search_date} -> {best_confirmed.return_date}).")
                best = best_confirmed

    cheapest_raw = min(connected, key=lambda o: o.price_total)
    if best is not cheapest_raw and _true_total(best, hotel, cfg) < _true_total(cheapest_raw, hotel, cfg):
        # Bugfix (Roadmap Runde 2, Punkt 1.6): delta_word(0) liefert "0
        # Hotelnaechte weniger" - Unsinn, wenn beide Optionen gleich viele
        # Naechte brauchen. Klausel dann ganz weglassen statt 0 zu beschoenigen.
        raw_delta = _hotel_nights_delta(cheapest_raw, cfg)
        nights_clause = f" braucht {delta_word(raw_delta)}," if raw_delta else ","
        notes.append(
            f"Guenstigste GESAMT-Kombination ist nicht der guenstigste Einzelflug: "
            f"{cheapest_raw.price_total:.0f} EUR ({cheapest_raw.search_date} -> "
            f"{cheapest_raw.return_date}){nights_clause} "
            f"dadurch in Summe teurer als die gewaehlte Option ({best.price_total:.0f} EUR, "
            f"{best.search_date} -> {best.return_date}).")
    delta_chosen = _hotel_nights_delta(best, cfg)
    if delta_chosen and not _hotel_for_flight(best, hotel, cfg)[2]:
        notes.append(
            f"Kombination {best.search_date} -> {best.return_date} braucht "
            f"{delta_word(delta_chosen)} als die konfigurierte Standard-Kombination "
            f"({cfg.trip.nights} Naechte) - Hotelanteil wurde mit dem Preis/Nacht "
            f"entsprechend hoch-/runtergerechnet.")
    if best.trip_type == "multi_city":
        notes.append(f"Guenstigste Option ist ein Multi-City-Ticket "
                     f"(Hinflug {best.origin}, Rueckflug nach {best.destination}) - "
                     f"unterschiedliche Flughaefen, trotzdem EIN zusammenhaengendes Ticket.")
    if best.pax_mode.startswith("split_"):
        shape = best.pax_mode.removeprefix("split_").replace("_", "+")
        notes.append(f"Guenstigste Option sind {len(shape.split('+'))} GETRENNTE Tickets "
                     f"({shape} Personen) statt einer gemeinsamen Buchung - kein "
                     f"gemeinsamer Umbuchungsschutz bei Verspaetung, dafuer in Summe guenstiger. "
                     f"{best.price_confidence} Reihenfolge: erst Familie 1 buchen, dann den "
                     f"Preis fuer Familie 2 SOFORT neu pruefen (kann inzwischen teurer sein).")
    return best, notes


def delta_word(delta: int) -> str:
    n = abs(delta)
    return f"{n} Hotelnacht{'e' if n != 1 else ''} mehr" if delta > 0 else f"{n} Hotelnacht{'e' if n != 1 else ''} weniger"


def _find_return_reference(flights: list[FlightOffer],
                           chosen: FlightOffer | None) -> FlightOffer | None:
    """Nur fuer Round-Trip: Google zeigt beim Round-Trip selbst nur den
    Hinflug im Detail. Eine separate Einzelrichtungs-Rueckflugsuche (selber
    Flughafen/Zeitraum) gibt ein plausibles Routing-Beispiel - rein
    informativ, NICHT Teil des Tickets/Preises."""
    if not chosen or chosen.trip_type != "round_trip":
        return None
    candidates = [o for o in flights
                 if o.trip_type == "one_way" and o.direction == "return"
                 and not o.excluded and o.price_total > 0
                 and o.destination == chosen.origin]
    if not candidates:
        return None
    exact = [o for o in candidates if chosen.return_date and o.search_date == chosen.return_date]
    pool = exact or candidates
    return min(pool, key=lambda o: o.price_total)


def build_verdict(flights: list[FlightOffer], hotels: list[HotelOffer],
                  packages: list[PackageOffer], cfg: Config) -> Verdict:
    v = Verdict(currency=cfg.trip.currency)

    # Hotel + Flug werden GEMEINSAM gewaehlt: je Flug-Datumspaar das zu genau
    # diesem Paar passende Hotel (Checkin = Abflug+1, Checkout = Rueckflug),
    # siehe _hotel_for_flight. Innerhalb eines Zeitraums gilt die Qualitaets-
    # stufe konkreter Scrape > manuelle Referenz > Floor-Richtwert.
    hotel_ok = [h for h in hotels if h.ok and h.price_total and h.price_total > 0]
    v.flight, notes = _pick_flight(flights, hotel_ok, cfg)
    v.notes += notes
    if v.flight:
        v.flight_total = round(v.flight.price_total, 2)
        v.return_reference = _find_return_reference(flights, v.flight)

    if hotel_ok:
        if v.flight:
            v.hotel, price, v.hotel_exact = _hotel_for_flight(v.flight, hotel_ok, cfg)
        else:
            v.hotel = _best_hotel(hotel_ok)
            price = v.hotel.price_total
        tier = _hotel_tier(v.hotel)
        if tier == 1:
            v.notes.append(f"Hotelpreis = manuelle Referenz '{v.hotel.source}' "
                           f"({v.hotel.price_total:.0f}). Automatische Quellen liefern "
                           f"nur Floor-Richtwerte (Belegung nicht abbildbar).")
        elif tier == 2:
            v.notes.append(f"Hotelpreis {v.hotel.price_total:.0f} ist ein Floor-Richtwert "
                           f"(guenstigstes Zimmer x {cfg.trip.rooms} x {cfg.trip.nights} N, "
                           f"ohne 3-Pers.-Villa-Aufpreis) - via reference_offers praezisieren.")
        ci, co = _hotel_dates(v.hotel, cfg)
        v.hotel_checkin, v.hotel_checkout = ci, co
        if v.hotel_exact:
            v.notes.append(f"Hotel fuer genau {ci} -> {co} live abgefragt "
                           f"({v.hotel.source}, {v.hotel.price_total:.0f}).")
            nights = (date.fromisoformat(co) - date.fromisoformat(ci)).days
        else:
            delta = _hotel_nights_delta(v.flight, cfg) if v.flight else 0
            nights = cfg.trip.nights + (delta or 0)
            if v.flight:
                v.notes.append(f"Hotel fuer {(v.flight.search_date + timedelta(days=1)).isoformat()} "
                               f"-> {v.flight.return_date} liegt nicht live vor, aus dem "
                               f"Standardzeitraum ({ci} -> {co}) umgerechnet (Schaetzung).")
        v.hotel_total = round(price, 2)
        v.nights_used = nights
    else:
        v.notes.append("Kein Hotelpreis verfuegbar - Santiburi ggf. manuell pruefen.")

    if v.flight_total is not None and v.hotel_total is not None:
        v.separate_total = round(v.flight_total + v.hotel_total, 2)

    pkg_ok = [p for p in packages if p.ok and p.price_total and p.price_total > 0]
    if pkg_ok:
        v.package = min(pkg_ok, key=lambda p: p.price_total)
        v.package_total = round(v.package.price_total, 2)
    else:
        v.notes.append("Keine Pauschalreise gefunden (fuer Mai 2027 oft noch "
                       "nicht buchbar).")

    if v.separate_total is not None and v.package_total is not None:
        v.delta = round(v.package_total - v.separate_total, 2)
        v.winner = "pauschalreise" if v.delta < 0 else "einzelbuchung"
    elif v.separate_total is not None:
        v.winner = "einzelbuchung"
    elif v.package_total is not None:
        v.winner = "pauschalreise"

    log.info("Verdict: Flug=%s (%s) Hotel=%s Einzel=%s Pauschal=%s Winner=%s",
             v.flight_total, v.flight.trip_type if v.flight else None, v.hotel_total,
             v.separate_total, v.package_total, v.winner)
    return v
