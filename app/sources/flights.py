"""Flugquelle: Google Flights via die inoffizielle Lib ``fast-flights``.

Einzige frei nutzbare Quelle mit brauchbarer Datentiefe (Flugzeugtyp,
Segmentzeiten, Umsteigeflughaefen, Preis, CO2). Kiwi Tequila (nur auf
Einladung) und Amadeus Self-Service (17.07.2026 abgeschaltet) fallen weg.

Es werden AUSSCHLIESSLICH zusammenhaengende Buchungen (ein Ticket) gesucht:
  * **Round-Trip** - gleicher Flughafen Hin/Rueck.
  * **Multi-City** - Hinflug-Airport != Rueckflug-Airport (z.B. Zuerich hin,
    Muenchen zurueck), trotzdem EIN Ticket mit geschuetzten Anschluessen.
    Das ist die saubere Antwort auf "Hin/Rueck muessen nicht gleich sein" -
    statt zwei Einzelrichtungs-Tickets zu summieren (die niemand kaufen will,
    weil bei Verspaetung keine Umbuchungspflicht besteht), fragt das Tool
    genau diese eine Buchung ab.
Reine Einzelrichtungs-Suchen (``oneway_legs``) gibt es nur noch optional fuer
Debug-Zwecke, sie werden nirgends als Buchungsoption angezeigt.

Weitere Design-Entscheidungen:
  * **EU-Consent:** ohne Cookie leitet Google auf consent.google.com um -> wir
    holen das HTML selbst (primp mit Chrome-Impersonation + Cookie ``SOCS=CAI``)
    und geben es an den fast-flights-Parser.
  * **Basissuche** laeuft mit 1 Pax, Preis x ``trip.persons``
    (``price_is_total_for_all_pax: false``) - primp liefert fuer
    Mehrpersonen-Anfragen oft nur einen Bruchteil der echten Ergebnisse
    (siehe ``flights_group_browser.py`` Modul-Docstring). Die echte
    Personenzahl wird stattdessen dort per Browser fuer die guenstigsten
    Kandidaten nachverifiziert (``pax_mode="group"``/``"split_4_4"``).
  * **Blockade-Erkennung:** "unusual traffic"/Sorry-Seite -> eigener Fehler,
    eine Wiederholung nach Pause, danach als Quelle-Fehler im Health sichtbar
    (nicht als "keine Fluege" verschleiert).
  * **Nur durchgehende Tickets** (``single_ticket_only``): getrennte Tickets /
    Self-Transfer werden bereits bei der Suche ausgeblendet.
"""
from __future__ import annotations

import random
import time as _time
from datetime import date, datetime
from zoneinfo import ZoneInfo

from ..config import Config
from ..geo import get_airport
from ..logging_setup import get_logger
from ..offers import FlightOffer, Segment

log = get_logger("source.flights")

SEARCH_PAX = 1  # Google liefert fuer Gruppen nichts -> 1 Pax, Preis hochrechnen
_CONSENT_COOKIE = {"SOCS": "CAI"}
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
# Nur DIESER Fehler ist die von fast-flights selbst dokumentierte, saubere
# "keine Fluege"-Signatur (Google sendet explizit "errorHasStatus: true").
_EMPTY_RESULT_ERRORS = ("FlightsNotFound",)
# ACHTUNG: TypeError/IndexError/KeyError/AttributeError NICHT hierher packen!
# Verifiziert (11.09.26): ein TypeError beim Parsen ("payload[3] is None")
# trat reproduzierbar fuer STR/MUC auf, obwohl echte Fluege existieren
# (Stuttgart->USM 14.-28.05.2027 ab 1.043EUR p.P., manuell in Google Flights
# nachgeprueft) - vermutlich weil Google die Ergebnisse bei komplexeren
# Routings (mehr Umsteigeoptionen) beim ersten SSR-Request noch nicht fertig
# berechnet hat. Das ist ein TRANSIENTER Parser-Fehler, keine echte Leere -
# wird unten wie eine Blockade behandelt (eine Wiederholung, sonst als
# failed_query sichtbar), NIE stillschweigend als "keine Fluege" verschluckt.
_RETRYABLE_PARSE_ERRORS = ("TypeError", "IndexError", "KeyError", "AttributeError")


class FlightBlocked(RuntimeError):
    """Google hat die Anfrage geblockt (Sorry-/Captcha-/Consent-Seite)."""


try:
    from fast_flights import FlightQuery, Passengers, create_query, get_flights
    from fast_flights.integrations.base import FetchIntegration
    from fast_flights.querying import Query

    _HAVE_FF = True
except Exception as exc:  # pragma: no cover
    _HAVE_FF = False
    _FF_IMPORT_ERROR = repr(exc)
    FetchIntegration = object  # type: ignore

_SEAT_MAP = {"economy": "economy", "premium-economy": "premium-economy",
             "business": "business", "first": "first"}


class _ConsentFetcher(FetchIntegration):  # type: ignore[misc]
    """Holt das Flights-HTML mit Consent-Cookie; erkennt Blockaden."""

    URL = "https://www.google.com/travel/flights"

    def __init__(self, proxy: str | None = None) -> None:
        self._proxy = proxy
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import primp

                self._client = primp.Client(
                    impersonate="chrome_146", impersonate_os="windows",
                    cookie_store=True, follow_redirects=True, timeout=35,
                    proxy=self._proxy,
                )
                try:
                    self._client.set_cookies("https://www.google.com", _CONSENT_COOKIE)
                except Exception:  # pragma: no cover
                    pass
                self._mode = "primp"
            except Exception:  # pragma: no cover
                import httpx

                self._client = httpx.Client(
                    follow_redirects=True, timeout=35, proxy=self._proxy,
                    headers={"User-Agent": _UA,
                             "Accept-Language": "de-DE,de;q=0.9,en;q=0.8"},
                    cookies=_CONSENT_COOKIE,
                )
                self._mode = "httpx"
        return self._client

    def fetch_html(self, q) -> str:  # noqa: ANN001
        params = q.params() if isinstance(q, Query) else {"q": q}
        client = self._get_client()
        r = client.get(self.URL, params=params)
        text = r.text
        low = text.lower()
        blocked = ("/sorry/" in low or "unusual traffic" in low
                   or "detected unusual" in low
                   or ("consent.google" in str(getattr(r, "url", "")))
                   or ("ds:1" not in text and len(text) < 40000))
        if blocked:
            raise FlightBlocked(f"geblockt ({self._mode}, {len(text)} B)")
        return text


def _mk_dt(date_tuple, time_tuple, tz: str, fallback: date) -> datetime:
    try:
        parts = (list(date_tuple) + [None, None, None])[:3]
        y = parts[0] or fallback.year
        m = parts[1] or fallback.month
        d = parts[2] or fallback.day
        tp = (list(time_tuple) + [0, 0])[:2]
        return datetime(int(y), int(m), int(d), int(tp[0] or 0), int(tp[1] or 0),
                        tzinfo=ZoneInfo(tz))
    except Exception:
        return datetime(fallback.year, fallback.month, fallback.day, 0, 0,
                        tzinfo=ZoneInfo(tz))


def _build_segments(single_flights: list, airlines: list[str],
                    search_date: date) -> list[Segment]:
    segs: list[Segment] = []
    for i, sf in enumerate(single_flights):
        frm = getattr(sf.from_airport, "code", "") or ""
        to = getattr(sf.to_airport, "code", "") or ""
        dtz, atz = get_airport(frm).tz, get_airport(to).tz
        dep = _mk_dt(sf.departure.date, sf.departure.time, dtz, search_date)
        arr = _mk_dt(sf.arrival.date, sf.arrival.time, atz, search_date)
        dur = int(getattr(sf, "duration", 0) or 0)
        if dur <= 0:
            dur = max(0, int((arr - dep).total_seconds() // 60))
        seg_airline = airlines[i] if len(airlines) == len(single_flights) else ""
        segs.append(Segment(
            from_airport=frm, to_airport=to, departure=dep, arrival=arr,
            departure_tz=dtz, arrival_tz=atz, duration_minutes=dur,
            airline=seg_airline, plane_type=getattr(sf, "plane_type", "") or "",
        ))
    return segs


def _to_offer(fl, *, trip_type: str, direction: str, origin: str,
              destination: str, search_date: date, return_date: date | None,
              cfg: Config, deep_link: str, pax_mode: str = "estimated") -> FlightOffer | None:
    # pax_mode ist hier IMMER "estimated" (collect_flights() ruft _to_offer
    # nur fuer die 1-Pax-Suche auf) - die echten "group"/"split_*"-Angebote
    # baut flights_group_browser.py direkt als FlightOffer, ohne _to_offer.
    # (13.09.26 aufgeraeumt: fruehere "group"/"split_half"-Zweige hier waren
    # seit der Aufteilung in flights_group_browser.py toter Code - nie
    # erreichbar, weil kein Aufrufer je einen anderen pax_mode uebergibt.)
    persons = cfg.trip.persons
    try:
        raw_price = float(fl.price or 0)
    except Exception:
        raw_price = 0.0
    if raw_price <= 0:
        return None
    if cfg.flight_constraints.price_is_total_for_all_pax:
        total, per_person = raw_price, raw_price / persons
    else:
        per_person, total = raw_price, raw_price * persons

    airlines = list(getattr(fl, "airlines", []) or [])
    single_flights = list(getattr(fl, "flights", []) or [])
    all_segs = _build_segments(single_flights, airlines, search_date)
    if not all_segs:
        return None

    # Bei multi_city liefert Google BEIDE Strecken in einer Liste -> am
    # Zielflughafen (USM) in Hin- und Rueckstrecke aufteilen. Der Zeitraum
    # dazwischen ist der Urlaub selbst, KEIN Layover.
    hub = cfg.trip.destination_airport
    out_segs, ret_segs = all_segs, []
    if trip_type == "multi_city":
        split = next((i for i, s in enumerate(all_segs) if s.to_airport == hub), None)
        if split is not None and split + 1 < len(all_segs):
            out_segs, ret_segs = all_segs[: split + 1], all_segs[split + 1:]

    try:
        carbon = int(getattr(getattr(fl, "carbon", None), "emission", None))
    except Exception:
        carbon = None

    end_code = (ret_segs[-1].to_airport if ret_segs else out_segs[-1].to_airport)
    return FlightOffer(
        source="google_flights(fast-flights)", direction=direction,
        trip_type=trip_type, return_date=return_date,
        origin=out_segs[0].from_airport or origin,
        destination=end_code or destination,
        search_date=search_date,
        price_total=round(total, 2), price_per_person=round(per_person, 2),
        currency=cfg.trip.currency, airlines=airlines,
        segments=out_segs, return_segments=ret_segs,
        deep_link=deep_link, carbon_grams=carbon, pax_mode=pax_mode,
    )


def _make_query(legs: list, trip: str, cfg: Config, pax: int = SEARCH_PAX):
    fc = cfg.flight_constraints
    q = create_query(
        flights=legs, seat=_SEAT_MAP.get(fc.seat_class, "economy"), trip=trip,
        passengers=Passengers(adults=pax),
        language=cfg.sources.flights.language or "", currency=cfg.trip.currency,
        max_stops=fc.max_stops_per_direction,
        hide_separate_and_self_transfer=fc.single_ticket_only,
        # 13.09.26 Nutzervorgabe: nur Tarife MIT Aufgabegepaeck vergleichen.
        # checked_bags rechnet einen etwaigen Gepaeck-Aufpreis in den Preis
        # ein, exclude_basic_economy nimmt reine Light-Tarife ganz raus.
        checked_bags=fc.checked_bags_included_in_search,
        exclude_basic_economy=fc.exclude_basic_economy,
    )
    try:
        return q, q.url()
    except Exception:
        return q, ""


def _out_leg(origin: str, d: date, cfg: Config):
    # earliest_departure_hour ist nur Google-seitig ein grobes Vorfilter
    # (nur volle Stunden) - der exakte, airport-spezifische Cutoff (z.B.
    # STR 16:30) wird zusaetzlich hart in route_filter.py Rule 1 geprueft.
    return FlightQuery(date=d.isoformat(), from_airport=origin,
                       to_airport=cfg.trip.destination_airport,
                       max_stops=cfg.flight_constraints.max_stops_per_direction,
                       earliest_departure_hour=cfg.flight_constraints.earliest_departure_for(origin).hour)


def _ret_leg(dest: str, d: date, cfg: Config):
    return FlightQuery(date=d.isoformat(), from_airport=cfg.trip.destination_airport,
                       to_airport=dest,
                       max_stops=cfg.flight_constraints.max_stops_per_direction)


def _run_query(q, fetcher, cfg) -> list:
    """get_flights mit einer Wiederholung bei Blockade ODER einem
    verdaechtigen (moeglicherweise transienten) Parser-Fehler - siehe
    ``_RETRYABLE_PARSE_ERRORS``. Erst wenn die Wiederholung ebenfalls
    scheitert, wird der Fehler nach oben gereicht (dort als failed_query
    sichtbar, NIE als "keine Fluege" interpretiert)."""
    try:
        return list(get_flights(q, integration=fetcher))
    except FlightBlocked as exc:
        # 14.09.26 (externe Review, Punkt B): ein einzelner Retry nach fester
        # Pause reicht bei "unusual traffic" oft nicht - Google haelt eine
        # Blockade meist laenger als 75s durch. Exponentielles Backoff ueber
        # mehrere Versuche statt nur einen, Obergrenze verhindert, dass ein
        # einzelner Lauf ewig haengt.
        base = cfg.sources.flights.retry_blocked_after_seconds
        max_attempts = cfg.sources.flights.retry_blocked_max_attempts
        last_exc: Exception = exc
        for attempt in range(1, max_attempts + 1):
            wait = min(base * (2 ** (attempt - 1)), 600)
            log.warning("Blockiert (Versuch %d/%d) - Wiederholung in %.0fs",
                       attempt, max_attempts, wait)
            _time.sleep(wait)
            try:
                return list(get_flights(q, integration=fetcher))
            except FlightBlocked as exc2:
                last_exc = exc2
        raise last_exc
    except Exception as exc:
        if type(exc).__name__ not in _RETRYABLE_PARSE_ERRORS:
            raise
        wait = min(cfg.sources.flights.retry_blocked_after_seconds, 20)
        log.warning("Verdaechtiger Parser-Fehler (%s) - moeglicherweise noch "
                   "nicht fertig berechnet, eine Wiederholung in %.0fs: %s",
                   type(exc).__name__, wait, exc)
        _time.sleep(wait)
        return list(get_flights(q, integration=fetcher))  # wirft ggf. erneut


def _browser_fallback_offers(cfg: Config, meta: dict, deep_group: str) -> list[FlightOffer]:
    """Playwright-Fallback fuer primp-Ausfaelle bei einzelnen Round-Trip-
    Kombis (siehe A4-Kommentar am Aufrufer). Baut aus den ECHTEN Karten
    (siehe flight_cards.py) dieselben "estimated" 1-Pax-hochgerechneten
    Angebote wie der primp-Pfad - nur eben aus dem Browser statt aus einem
    fehlgeschlagenen HTTP-Request."""
    from .scraper_base import playwright_available

    ok, err = playwright_available()
    if not ok:
        log.warning("Browser-Fallback nicht verfuegbar: %s", err)
        return []

    import asyncio

    from .flight_cards import build_approx_segments
    from .flights_browser_search import cheapest_round_trip_cards

    origin, s_date, r_date = meta["origin"], meta["search_date"], meta["return_date"]

    async def _run():
        cards, _ = await cheapest_round_trip_cards(cfg, origin, s_date, r_date, SEARCH_PAX)
        return cards

    try:
        try:
            cards = asyncio.run(_run())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                cards = loop.run_until_complete(_run())
            finally:
                loop.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("Browser-Fallback fehlgeschlagen: %s: %s", type(exc).__name__, exc)
        return []

    persons = cfg.trip.persons
    out = []
    for card in cards:
        segs = build_approx_segments(card["origin"], card["dest"], card["layovers"],
                                     card["dep_time"], card["duration_min"], s_date)
        out.append(FlightOffer(
            source="google_flights(playwright-fallback)", direction=meta["direction"],
            trip_type=meta["trip_type"], origin=origin, destination=meta["destination"],
            search_date=s_date, return_date=r_date,
            price_total=round(card["price"] * persons, 2), price_per_person=round(card["price"], 2),
            currency=cfg.trip.currency, airlines=card["airlines"], segments=segs,
            deep_link=deep_group, pax_mode="estimated", segment_times_approximate=True,
        ))
    return out


def _flag_duplicate_estimates(offers: list[FlightOffer], key: tuple, price: float,
                              reason: str) -> int:
    """Markiert ALLE "estimated"-Angebote mit genau diesem Key+Preis als vom
    Gruppen-Check widerlegt - nicht nur die eine Objekt-Referenz, die
    ``best_by_key`` zufaellig zuerst gesehen hat.

    Google liefert dieselbe guenstigste Option oft MEHRFACH als identische
    Duplikate in EINER Antwort (verifiziert 12.09.26: FRA-USM Qatar
    908EUR/p.P. kam 3x im selben Suchergebnis). Ohne diese Funktion wird nur
    EINE der Kopien geflaggt - die anderen bleiben ``excluded=False`` und
    dedup_flights() zieht (seit dessen eigenem Bugfix 11.09.26: ein
    excluded-Angebot darf nie ein gueltiges verdraengen) genau so ein
    unmarkiertes Duplikat der bevorzugten, korrekt geflaggten Kopie vor - die
    laengst widerlegte Buchung waere trotzdem wieder "die guenstigste"
    gewesen (live reproduziert in Run #7, 12.09.26). Gibt die Anzahl
    geflaggter Duplikate zurueck (mind. 1, wenn ``estimated`` selbst passt).
    """
    n = 0
    for o in offers:
        if (o.pax_mode == "estimated" and o.price_total == price
                and (o.trip_type, o.origin, o.destination, o.search_date, o.return_date) == key):
            o.group_check_unconfirmed = reason
            n += 1
    return n


def collect_flights(cfg: Config, proxy: str | None = None) -> tuple[list[FlightOffer], dict]:
    if not _HAVE_FF:
        return [], {"ok": False, "count": 0, "queries": 0, "failed_queries": 0,
                    "error": f"fast-flights nicht importierbar: {_FF_IMPORT_ERROR}"}

    t, fs = cfg.trip, cfg.sources.flights
    lo, hi = fs.request_delay_seconds
    fetcher = _ConsentFetcher(proxy=proxy)

    Job = tuple  # (label, legs, trip, meta)
    jobs: list[Job] = []

    # 1) Round-Trip, gleicher Flughafen Hin/Rueck
    if fs.roundtrip:
        for origin in t.origin_airports:
            for out_d, ret_d in t.rt_date_pairs():
                jobs.append((
                    f"RT {origin} {out_d}/{ret_d}",
                    [_out_leg(origin, out_d, cfg), _ret_leg(origin, ret_d, cfg)],
                    "round-trip",
                    {"trip_type": "round_trip", "direction": "round_trip",
                     "origin": origin, "destination": t.destination_airport,
                     "search_date": out_d, "return_date": ret_d},
                ))

    # 2) Multi-City: Hinflug-Airport != Rueckflug-Airport, EIN Ticket.
    #    Standardmaessig nur das primaere Datumspaar (Airport-Matrix waechst
    #    sonst schnell): multicity_all_date_pairs: true schaltet alle frei.
    if fs.multicity:
        pairs = t.rt_date_pairs() if fs.multicity_all_date_pairs else t.rt_date_pairs()[:1]
        for a in t.origin_airports:
            for b in t.origin_airports:
                if a == b:
                    continue
                for out_d, ret_d in pairs:
                    jobs.append((
                        f"MC {a}->USM->{b} {out_d}/{ret_d}",
                        [_out_leg(a, out_d, cfg), _ret_leg(b, ret_d, cfg)],
                        "multi-city",
                        {"trip_type": "multi_city", "direction": "multi_city",
                         "origin": a, "destination": b,
                         "search_date": out_d, "return_date": ret_d},
                    ))

    # 3) Einzelrichtungs-Legs - NUR Debug/Referenz, keine Buchungsoption.
    if fs.oneway_legs:
        out_dates = t.outbound_dates if fs.oneway_legs_all_dates else t.outbound_dates[:1]
        ret_dates = t.return_dates if fs.oneway_legs_all_dates else t.return_dates[:1]
        for origin in t.origin_airports:
            for d in out_dates:
                jobs.append((f"OW {origin}->USM {d}", [_out_leg(origin, d, cfg)],
                             "one-way",
                             {"trip_type": "one_way", "direction": "outbound",
                              "origin": origin, "destination": t.destination_airport,
                              "search_date": d, "return_date": None}))
        for dest in t.origin_airports:
            for d in ret_dates:
                jobs.append((f"OW USM->{dest} {d}", [_ret_leg(dest, d, cfg)],
                             "one-way",
                             {"trip_type": "one_way", "direction": "return",
                              "origin": t.destination_airport, "destination": dest,
                              "search_date": d, "return_date": None}))

    offers: list[FlightOffer] = []
    failed = blocked = 0
    first_error = ""
    # Anfragen, die auch nach Wiederholung scheitern (Blockade oder der
    # verifizierte STR/MUC-Parser-Fehler, siehe _RETRYABLE_PARSE_ERRORS):
    # hier NIE stillschweigend "keine Fluege" behaupten, sondern den
    # fertigen Deep-Link fuers manuelle Nachschauen aufheben.
    manual_check: list[dict] = []
    for i, (label, legs, trip, meta) in enumerate(jobs):
        # Die Suche selbst laeuft bewusst mit 1 Pax (SEARCH_PAX) - primp
        # liefert fuer Mehrpersonen-Anfragen unzuverlaessige Teilergebnisse
        # (siehe flights_group_browser.py Modul-Docstring). Der Link, den der
        # Nutzer anklickt, soll aber die echte Personenzahl zeigen (Nutzer-
        # Fund 13.09.26: "Links fuehren immer noch zu Ergebnissen fuer eine
        # Person") - dafuer reicht ein zweiter, rein lokal kodierter Query
        # (kein zusaetzlicher Request, nur eine andere tfs-URL).
        q, deep = _make_query(legs, trip, cfg)
        _, deep_group = _make_query(legs, trip, cfg, pax=cfg.trip.persons)
        try:
            raw = _run_query(q, fetcher, cfg)
            got = 0
            for fl in raw:
                off = _to_offer(fl, cfg=cfg, deep_link=deep_group, **meta)
                if off:
                    offers.append(off)
                    got += 1
            log.info("%s: %d Angebote", label, got)
        except FlightBlocked as exc:
            blocked += 1
            first_error = first_error or f"{label}: {exc}"
            log.warning("%s: %s", label, exc)
            manual_check.append({"label": label, "reason": "blockiert", "deep_link": deep_group})
        except Exception as exc:  # noqa: BLE001
            name = type(exc).__name__
            if name in _EMPTY_RESULT_ERRORS:
                log.info("%s: keine Fluege (leeres Ergebnis / noch nicht im Verkauf)", label)
            elif trip == "round-trip" and name in _RETRYABLE_PARSE_ERRORS:
                # A4 (externe Review 14.09.26): primp scheitert fuer manche
                # Kombis reproduzierbar mit einem der bekannten transienten
                # Parser-Fehler (verifiziert fuer STR/MUC), obwohl echte
                # Fluege existieren - Browser-Fallback statt die Route
                # komplett aus dem Vergleich zu nehmen.
                fb_offers = _browser_fallback_offers(cfg, meta, deep_group)
                if fb_offers:
                    offers.extend(fb_offers)
                    log.info("%s: primp gescheitert (%s) - Browser-Fallback lieferte %d Angebote",
                             label, name, len(fb_offers))
                else:
                    failed += 1
                    first_error = first_error or f"{label}: {name}: {exc} (Browser-Fallback auch leer)"
                    log.warning("%s: primp UND Browser-Fallback gescheitert", label)
                    manual_check.append({"label": label,
                                        "reason": f"{name}: {exc} (auch per Browser kein Preis)",
                                        "deep_link": deep_group})
            else:
                # Auch nach der Wiederholung in _run_query gescheitert (egal ob
                # Blockade oder Parser-Fehler) - NIE als "keine Fluege"
                # verschleiern, sondern als echten Fehler zaehlen/melden UND
                # einen manuellen Nachschau-Link mitgeben.
                failed += 1
                first_error = first_error or f"{label}: {name}: {exc}"
                log.warning("%s: Fehler nach Wiederholung %s: %s", label, name, exc)
                manual_check.append({"label": label, "reason": f"{name}: {exc}", "deep_link": deep_group})
        if i < len(jobs) - 1:
            _time.sleep(random.uniform(lo, hi))

    # ---- Gruppen-/Split-Check: siehe sources/flights_group_browser.py.
    # NICHT mehr per primp (siehe dortiger Modul-Docstring: primp liefert
    # fuer Mehrpersonen-Suchen oft nur einen Bruchteil der echten Ergebnisse
    # - verifiziert 13.09.26, primp fand fuer eine echte 8-Pax-Suche FRA-USM
    # nur 1 von 2 sichtbaren Angeboten und hielt faelschlich den TEUREREN
    # Fund fuer den guenstigsten - ein echter Browser zeigte sofort einen um
    # 1.261 EUR guenstigeren echten Treffer). collect_flights() waehlt hier
    # nur noch die Kandidaten aus (best_by_key), collector.py ruft danach
    # den browserbasierten Check auf denselben Objekten auf.
    group_checked = 0
    best_by_key: dict[tuple, FlightOffer] = {}
    if fs.group_check and t.persons > 1:
        candidates = [o for o in offers
                     if o.trip_type in ("round_trip", "multi_city") and not o.excluded]
        for o in candidates:
            key = (o.trip_type, o.origin, o.destination, o.search_date, o.return_date)
            if key not in best_by_key or o.price_total < best_by_key[key].price_total:
                best_by_key[key] = o

    rt_count = sum(1 for o in offers if o.trip_type == "round_trip")
    mc_count = sum(1 for o in offers if o.trip_type == "multi_city")
    health = {
        "ok": failed == 0 and blocked == 0,
        "count": len(offers), "roundtrip_count": rt_count, "multicity_count": mc_count,
        "queries": len(jobs), "failed_queries": failed, "blocked_queries": blocked,
        "error": first_error,
        "note": f"{SEARCH_PAX}-Pax-Suche, Preis x{t.persons}; nur zusammenhaengende Buchungen",
        "manual_check": manual_check,
        "group_checked": group_checked,
    }
    return offers, health
