"""Load and validate configuration.

Reiseparameter kommen aus ``config.yaml`` (Pfad via ``TRACKER_CONFIG`` ueber-
schreibbar), Secrets aus der Umgebung / ``.env``.  Beides wird hier zu einem
validierten, getippten Objekt zusammengefuehrt, das der Rest der App nutzt.
"""
from __future__ import annotations

import os
from datetime import date, time
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("TRACKER_CONFIG", BASE_DIR / "config.yaml"))


# --------------------------------------------------------------------------- #
# YAML-Modelle
# --------------------------------------------------------------------------- #
class ReferenceOffer(BaseModel):
    """Ein selbst gefundener Preis (z.B. CHECK24), den der Tracker als
    vollwertiges Angebot mitfuehrt - er gewinnt den Vergleich, solange keine
    automatische Quelle ihn unterbietet, und dient als Alarm-Basis."""

    label: str
    category: Literal["hotel", "package"] = "hotel"
    price_total: float                    # Gesamt fuer alle Pax / alle Zimmer
    currency: str = "EUR"
    url: str = ""
    note: str = ""
    operator: str = ""                    # nur category=package
    dep_airport: str = ""
    board: str = ""


class HotelCfg(BaseModel):
    name: str
    room_type_hint: str = ""
    booking_url: str = ""
    expedia_url: str = ""
    official_url: str = ""
    # CHECK24-Hoteleintrag "<Name>-<ID>" aus der Detail-URL
    # (hotel.check24.de/search/<slug_id>/<checkin>/<checkout>/[...]/hotel.html).
    # Einmalig per Suche auf hotel.check24.de ermitteln.
    check24_slug_id: str = ""
    # CHECK24-PAUSCHALREISE nutzt eine ANDERE interne Hotel-/Gebiets-ID als
    # der reine Hotelvergleich oben - einmalig per Suche auf
    # urlaub.check24.de (Reiter "Pauschalreisen") aus der Ergebnis-URL
    # ermitteln (areaId=... & hotelId=...).
    check24_package_hotel_id: str = ""
    check24_package_area_id: str = ""


class TripCfg(BaseModel):
    label: str
    persons: int = Field(gt=0)
    rooms: int = Field(gt=0)
    max_persons_per_room: int = Field(gt=0)
    nights: int = Field(gt=0)
    currency: str = "EUR"
    destination_airport: str
    final_hub: str = "BKK"
    origin_airports: list[str]
    outbound_dates: list[date]
    return_dates: list[date]
    # Tatsaechlicher Hotel-Zeitraum - NICHT zwangslaeufig identisch mit dem
    # ersten Flugdatum: bei einem Nachtflug/Ankunft am Folgetag beginnt der
    # Hotelaufenthalt erst dort. Beispiel: Hinflug 14.05. (Abflug DE), Ankunft
    # in Thailand am 15.05. -> hotel_checkin = 2027-05-15.
    hotel_checkin: date
    hotel_checkout: date
    # Sinnvolle Hin/Rueck-Paare fuer die Round-Trip-Suche (ein Ticket).
    # Leer -> wird aus outbound_dates[0]+return_dates[0] und
    # outbound_dates[-1]+return_dates[-1] abgeleitet.
    date_pairs: list[list[date]] = []
    hotel: HotelCfg
    # Selbst recherchierte Preise (CHECK24 & Co.), die als vollwertige Angebote
    # in Vergleich/Trend/Alarm einfliessen. Automatische Quellen sind bei einem
    # so speziellen Setup (3 Villen, 8 Pers., 14 Naechte, Mai 2027) unzuverlaessig.
    reference_offers: list[ReferenceOffer] = []

    @field_validator("origin_airports")
    @classmethod
    def _upper(cls, v: list[str]) -> list[str]:
        return [x.strip().upper() for x in v]

    def rt_date_pairs(self) -> list[tuple[date, date]]:
        if self.date_pairs:
            return [(p[0], p[1]) for p in self.date_pairs]
        pairs = {(self.outbound_dates[0], self.return_dates[0]),
                 (self.outbound_dates[-1], self.return_dates[-1])}
        return sorted(pairs)


class FlightConstraintsCfg(BaseModel):
    max_stops_per_direction: int = 2
    # Fallback, falls ein Abflughafen nicht in
    # outbound_earliest_departure_by_airport steht.
    outbound_earliest_departure: time = time(15, 0)
    # Airport-spezifische fruehste Abflugzeit (Anreisezeit ab Wohnort
    # unterscheidet sich je Flughafen) - 13.09.26 Nutzervorgabe: STR ab
    # 16:30 (naeher dran), MUC/FRA/ZRH ab 17:00 (Anreise ab 14:30 Uhr aus
    # Schwaebisch Gmuend). Gilt nur fuer den Hinflug-Starttag, Rueckflug
    # unbeschraenkt (siehe route_filter.py Rule 1).
    outbound_earliest_departure_by_airport: dict[str, time] = {
        "STR": time(16, 30), "MUC": time(17, 0), "FRA": time(17, 0), "ZRH": time(17, 0),
    }
    # Nur durchgehende Tickets (ein Buchungscode, geschuetzte Anschluesse).
    # Getrennte Tickets / Self-Transfer werden bei der Suche ausgeblendet -
    # so ist bei Verspaetung die Airline fuer die Umbuchung zustaendig.
    single_ticket_only: bool = True
    max_detour_ratio: float = 1.7
    detour_min_direct_km: float = 1500
    max_total_travel_hours: float = 40
    # Mindestumstiegszeit: differenziert nach Umstiegsart (13.09.26
    # Nutzervorgabe - 45 Min war zu knapp). BKK->USM (innerhalb Thailand,
    # kurzer Zubringer) braucht weniger Puffer als ein internationaler
    # Umstieg (Immigration/Terminal-Wechsel etc.).
    min_layover_minutes_domestic_thailand: int = 90
    min_layover_minutes_international: int = 120
    max_layover_hours: float = 14
    price_is_total_for_all_pax: bool = False
    seat_class: Literal["economy", "premium-economy", "business", "first"] = "economy"
    # 1 Aufgabegepaeckstueck bei JEDER Google-Flights-Suche mit anfragen, so
    # dass der zurueckgegebene Preis den Gepaeck-Aufpreis (falls es ein
    # Light-Tarif waere) bereits einrechnet, statt Light-/Vollpreis-Tarife
    # unfair nebeneinander zu vergleichen (13.09.26 Nutzervorgabe: nur
    # Tarife MIT Gepaeck vergleichen). fast-flights/Google liefert keine
    # strukturierte "ist Gepaeck inkludiert"-Angabe im Ergebnis - dieser
    # Query-Parameter ist der zuverlaessige Weg, statt es aus dem Text zu raten.
    checked_bags_included_in_search: int = 1
    # Google direkt bitten, reine Basic-Economy-/Light-Tarife (kein
    # Gepaeck, keine Umbuchung) aus der Trefferliste zu nehmen - zusammen
    # mit checked_bags_included_in_search deckt das die Nutzervorgabe
    # "nur Tarife mit inkludiertem Aufgabegepaeck vergleichen" ab.
    exclude_basic_economy: bool = True

    # Airlines, die IMMER ausgeschlossen werden (Teilstring-Match, z.B.
    # "Austrian" faengt auch "Austrian Airlines") - gilt pro Segment, da
    # off.airlines alle Segment-Airlines einer Buchung sammelt (Rule 0 in
    # route_filter.py).
    excluded_airlines: list[str] = ["Austrian"]
    # Schalter fuer die Golfstaaten-Carrier (Qatar Airways, Etihad, Emirates,
    # Saudia, Gulf Air, Oman Air, Royal Jordanian) - wegen der Lage im Nahen
    # Osten optional ausschliessbar. Default aus, da noch nicht entschieden;
    # einfach auf true stellen, wenn die Entscheidung faellt.
    exclude_gulf_carriers: bool = False

    @field_validator("outbound_earliest_departure", mode="before")
    @classmethod
    def _parse_time(cls, v):
        if isinstance(v, str):
            h, m = v.split(":")[:2]
            return time(int(h), int(m))
        return v

    @field_validator("outbound_earliest_departure_by_airport", mode="before")
    @classmethod
    def _parse_time_dict(cls, v):
        if not isinstance(v, dict):
            return v
        out = {}
        for k, val in v.items():
            if isinstance(val, str):
                h, m = val.split(":")[:2]
                out[k] = time(int(h), int(m))
            else:
                out[k] = val
        return out

    def earliest_departure_for(self, airport: str) -> time:
        return self.outbound_earliest_departure_by_airport.get(
            airport, self.outbound_earliest_departure)


class ToggleCfg(BaseModel):
    enabled: bool = True


class FlightsSourceCfg(BaseModel):
    enabled: bool = True
    request_delay_seconds: tuple[float, float] = (10, 22)
    language: str = "de"
    # Round-Trip-Suche (Hin+Rueck als ein Ticket, gleicher Flughafen).
    roundtrip: bool = True
    # Multi-City (Hinflug-Airport != Rueckflug-Airport, trotzdem EIN Ticket).
    multicity: bool = True
    # Nur primaeres Datumspaar fuer die Multi-City-Airport-Matrix (sonst
    # waechst sie schnell: 4 Flughaefen -> 12 Kombinationen x Datumspaare).
    multicity_all_date_pairs: bool = False
    # Einzelrichtungs-Legs: NIE eine eigene Buchungsoption, aber liefern die
    # Rueckflug-Referenz (Routing-Beispiel) neben dem gewaehlten Round-Trip,
    # da Google beim Round-Trip selbst nur den Hinflug im Detail zeigt.
    oneway_legs: bool = True
    oneway_legs_all_dates: bool = False
    # Bei "unusual traffic"/Blockade: eine Wiederholung nach Pause.
    retry_blocked_after_seconds: float = 75
    # Echte Gruppensuche (Passengers(adults=trip.persons)) fuer die
    # guenstigsten Routen aus der 1-Pax-Hochrechnung: deckt Faelle auf, in
    # denen die guenstige Kombination fuer die volle Personenzahl gar nicht
    # (oder nur teurer) verfuegbar ist - verifiziert 11.09.26: FRA-USM zeigte
    # 1-Pax 908EUR p.P. (Qatar), die echte 8-Pax-Suche fand NUR Condor ab
    # 1150EUR p.P. Nur die guenstigsten group_check_top_n Kombinationen
    # werden zusaetzlich echt geprueft (sonst verdoppelt sich die Abfragezahl).
    group_check: bool = True
    group_check_top_n: int = 6
    # primp (reiner HTTP-Request) bekommt fuer Multi-City-Routen zu USM
    # reproduzierbar keine Daten (verifiziert 12.09.26, siehe
    # sources/flights_multicity_browser.py) - dieser Schalter aktiviert
    # einen langsameren, aber funktionierenden Playwright-Fallback NUR fuer
    # Multi-City (2 echte Seiten-Aufrufe je Route x Anzahl Kombinationen).
    multicity_browser_fallback: bool = True


class HotelsSourceCfg(BaseModel):
    # Google Hotels: schneller Floor-Richtwert (ein Request, kein Browser,
    # aggregiert ~20 OTAs), aber Belegung wird ignoriert (siehe google_hotels.py).
    google_hotels: ToggleCfg = ToggleCfg()
    # CHECK24: der GENAUE Live-Preis fuer die tatsaechliche Zimmeraufteilung -
    # sucht je Zimmergroesse einzeln (z.B. 1x "1 Zimmer/3 Erwachsene" + 1x
    # "1 Zimmer/2 Erwachsene") und summiert die guenstigsten Treffer x Anzahl
    # Zimmer dieser Groesse. Playwright, absichtlich langsam getaktet.
    check24: ToggleCfg = ToggleCfg()
    # Santiburi direkt, lastminute.com - gleicher Live-Deep-Link-Trick.
    santiburi_official: ToggleCfg = ToggleCfg()
    lastminute: ToggleCfg = ToggleCfg()
    # Expedia zeigt bei Playwright zuverlaessig einen Slider-Captcha (Bot-
    # Erkennung) - wird NICHT geloest (verboten). Deep-Link funktioniert
    # weiterhin fuer den manuellen Aufruf (Corporate-Rabatt), Default aus.
    expedia: ToggleCfg = ToggleCfg(enabled=False)
    # Booking als weiterer Fallback (fragil, Anti-Bot) - per Default aus:
    booking: ToggleCfg = ToggleCfg(enabled=False)
    # Pause zwischen den Teil-Suchen einer Live-Quelle (Sekunden, min/max) -
    # bewusst grosszuegig, Check laeuft eh nur alle paar Stunden.
    check24_delay_seconds: tuple[float, float] = (20, 45)


class PackagesSourceCfg(BaseModel):
    # CHECK24 Pauschalreisen: sucht wie beim Hotelvergleich je Zimmergroesse
    # einzeln (Flug+Hotel als ein Paket je Zimmer) und summiert.
    check24: ToggleCfg = ToggleCfg()
    check24_delay_seconds: tuple[float, float] = (20, 45)
    # Generische Portal-Scraper (Selektor-basiert, siehe specs.py):
    tui: ToggleCfg = ToggleCfg()
    dertour: ToggleCfg = ToggleCfg()
    alltours: ToggleCfg = ToggleCfg()
    rewe_reisen: ToggleCfg = ToggleCfg()
    sonnenklar: ToggleCfg = ToggleCfg()

    def enabled_names(self) -> list[str]:
        skip = {"check24_delay_seconds"}
        return [k for k, v in self.__dict__.items()
               if k not in skip and getattr(v, "enabled", False)]


class ItaMatrixSourceCfg(BaseModel):
    """ITA Matrix (Googles Fare-Research-Tool) - NICHT buchbar, daher eine
    eigene Recherche-Kategorie statt Teil der normalen Flugvergleichsliste.
    Siehe sources/flights_ita_matrix.py fuer die Deep-Link-Mechanik."""
    enabled: bool = True
    # None -> alle trip.origin_airports. ITA Matrix rechnet spuerbar
    # langsamer als Google Flights (haeufig 25-40s je Suche), deshalb hier
    # bewusst NUR das primaere Datumspaar, nicht die volle Matrix.
    origins: list[str] | None = None
    request_delay_seconds: tuple[float, float] = (15, 30)


class ScraperCfg(BaseModel):
    headless: bool = True
    nav_timeout_seconds: float = 45
    locale: str = "de-DE"
    screenshot_on_error: bool = True


class SourcesCfg(BaseModel):
    flights: FlightsSourceCfg = FlightsSourceCfg()
    ita_matrix: ItaMatrixSourceCfg = ItaMatrixSourceCfg()
    hotels: HotelsSourceCfg = HotelsSourceCfg()
    packages: PackagesSourceCfg = PackagesSourceCfg()
    scraper: ScraperCfg = ScraperCfg()


class ScheduleCfg(BaseModel):
    enabled: bool = True
    every_hours: float = 6
    run_on_start: bool = False
    jitter_seconds: int = 300


class TrendCfg(BaseModel):
    window_checks: int = 20
    cheap_threshold_pct: float = 4
    expensive_threshold_pct: float = 4


class AlertThresholds(BaseModel):
    flight_total: float | None = None
    hotel_total: float | None = None
    separate_total: float | None = None
    package_total: float | None = None


class AlertsCfg(BaseModel):
    enabled: bool = True
    thresholds: AlertThresholds = AlertThresholds()
    cooldown_hours: float = 12
    channels: list[Literal["email", "ntfy"]] = ["email", "ntfy"]


class Config(BaseModel):
    trip: TripCfg
    flight_constraints: FlightConstraintsCfg = FlightConstraintsCfg()
    sources: SourcesCfg = SourcesCfg()
    schedule: ScheduleCfg = ScheduleCfg()
    trend: TrendCfg = TrendCfg()
    alerts: AlertsCfg = AlertsCfg()


# --------------------------------------------------------------------------- #
# Secrets aus der Umgebung
# --------------------------------------------------------------------------- #
class Secrets(BaseModel):
    database_url: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "urlaub-tracker@example.com"
    smtp_starttls: bool = True
    alert_email_to: list[str] = []
    ntfy_url: str = "https://ntfy.sh"
    ntfy_topic: str | None = None
    ntfy_token: str | None = None
    trigger_token: str | None = None
    flights_proxy: str | None = None

    @classmethod
    def from_env(cls) -> "Secrets":
        _load_dotenv(BASE_DIR / ".env")
        g = os.environ.get
        return cls(
            database_url=g("DATABASE_URL") or None,
            smtp_host=g("SMTP_HOST") or None,
            smtp_port=int(g("SMTP_PORT", "587") or 587),
            smtp_user=g("SMTP_USER") or None,
            smtp_password=g("SMTP_PASSWORD") or None,
            smtp_from=g("SMTP_FROM", "urlaub-tracker@example.com"),
            smtp_starttls=(g("SMTP_STARTTLS", "true").lower() == "true"),
            alert_email_to=[x.strip() for x in g("ALERT_EMAIL_TO", "").split(",") if x.strip()],
            ntfy_url=g("NTFY_URL", "https://ntfy.sh"),
            ntfy_topic=g("NTFY_TOPIC") or None,
            ntfy_token=g("NTFY_TOKEN") or None,
            trigger_token=g("TRIGGER_TOKEN") or None,
            flights_proxy=g("FLIGHTS_PROXY") or None,
        )


def _load_dotenv(path: Path) -> None:
    """Minimaler .env-Loader (keine externe Abhaengigkeit)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
@lru_cache
def get_config() -> Config:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Konfiguration nicht gefunden: {CONFIG_PATH}. "
            "config.example.yaml nach config.yaml kopieren."
        )
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return Config.model_validate(raw)


@lru_cache
def get_secrets() -> Secrets:
    return Secrets.from_env()


def reload() -> None:
    get_config.cache_clear()
    get_secrets.cache_clear()
