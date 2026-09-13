"""Orchestrator: ein kompletter Preis-Check.

Ablauf:
  1. CheckRun anlegen
  2. Fluege holen -> harte Constraints -> Dedup
  3. Hotels holen (Playwright)
  4. Pauschalreisen holen (Playwright)
  5. Vergleichslogik (Einzelbuchung vs. Pauschal)
  6. Alles persistieren + BestSnapshot schreiben
  7. Schwellen pruefen -> ggf. Alarm
Kein Schritt darf den Lauf abbrechen; teilweise leere Quellen sind normal.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .config import get_config, get_secrets
from .database import get_session, init_db
from .logging_setup import get_logger, setup_logging
from .logic.combine import Verdict, build_verdict
from .logic.normalize import dedup_flights
from .logic.route_filter import apply_constraints
from .models import (
    BestSnapshot,
    CheckRun,
    FlightOfferRow,
    HotelOfferRow,
    PackageOfferRow,
)
from .notify import maybe_alert
from .offers import FlightOffer, HotelOffer, PackageOffer
from .sources import flights_group_browser, flights_ita_matrix, flights_multicity_browser
from .sources.flights import collect_flights
from .sources.hotels import collect_hotels
from .sources.packages import collect_packages

log = get_logger("collector")


def _flight_row(run_id: int, o: FlightOffer) -> FlightOfferRow:
    return FlightOfferRow(
        run_id=run_id, source=o.source, direction=o.direction,
        trip_type=o.trip_type, origin=o.origin,
        destination=o.destination, search_date=o.search_date.isoformat(),
        return_date=o.return_date.isoformat() if o.return_date else None,
        price_total=o.price_total, price_per_person=o.price_per_person,
        currency=o.currency, airlines=o.airlines,
        segments=[s.as_dict() for s in o.segments],
        return_segments=[s.as_dict() for s in o.return_segments], stops=o.stops,
        layover_airports=o.layover_airports, layover_minutes=o.layover_minutes,
        total_duration_minutes=o.total_duration_minutes, carbon_grams=o.carbon_grams,
        deep_link=o.deep_link, captured_at=o.captured_at,
        excluded=o.excluded, exclude_reason=o.exclude_reason,
        pax_mode=o.pax_mode,
    )


def _hotel_row(run_id: int, o: HotelOffer) -> HotelOfferRow:
    return HotelOfferRow(
        run_id=run_id, source=o.source, ok=o.ok, price_total=o.price_total,
        per_night=o.per_night, currency=o.currency, room_desc=o.room_desc,
        nights=o.nights, rooms=o.rooms, guests=o.guests, deep_link=o.deep_link,
        captured_at=o.captured_at, error=o.error, raw=o.raw,
        is_reference=o.is_reference,
    )


def _package_row(run_id: int, o: PackageOffer) -> PackageOfferRow:
    return PackageOfferRow(
        run_id=run_id, source=o.source, ok=o.ok, price_total=o.price_total,
        currency=o.currency, operator=o.operator, hotel_name=o.hotel_name,
        dep_airport=o.dep_airport, board=o.board, nights=o.nights,
        persons=o.persons, deep_link=o.deep_link, captured_at=o.captured_at,
        error=o.error, raw=o.raw, is_reference=o.is_reference,
    )


def _reference_offers(cfg) -> tuple[list[HotelOffer], list[PackageOffer]]:
    """config.trip.reference_offers -> vollwertige Angebote (is_reference)."""
    hotels: list[HotelOffer] = []
    packages: list[PackageOffer] = []
    for r in cfg.trip.reference_offers:
        if r.category == "hotel":
            hotels.append(HotelOffer(
                source=f"referenz:{r.label}"[:60], ok=True,
                price_total=r.price_total, currency=r.currency,
                room_desc=r.note, nights=cfg.trip.nights, rooms=cfg.trip.rooms,
                guests=cfg.trip.persons, per_night=round(r.price_total / max(1, cfg.trip.nights), 2),
                deep_link=r.url, error="", raw={"note": r.note, "manuell": True},
                is_reference=True,
            ))
        else:
            packages.append(PackageOffer(
                source=f"referenz:{r.label}"[:60], ok=True,
                price_total=r.price_total, currency=r.currency,
                operator=r.operator or r.label, hotel_name=cfg.trip.hotel.name,
                dep_airport=r.dep_airport, board=r.board, nights=cfg.trip.nights,
                persons=cfg.trip.persons, deep_link=r.url,
                raw={"note": r.note, "manuell": True}, is_reference=True,
            ))
    return hotels, packages


def _overall_status(health: dict) -> str:
    vals = list(health.values())
    if all(h.get("ok") for h in vals):
        return "ok"
    if any(h.get("count") for h in vals):
        return "partial"
    return "error"


def run_check(trigger: str = "manual") -> dict:
    setup_logging()
    init_db()
    cfg = get_config()
    sec = get_secrets()
    session = get_session()

    run = CheckRun(trigger=trigger, status="running")
    session.add(run)
    session.commit()
    log.info("=== CheckRun #%s (%s) gestartet ===", run.id, trigger)

    health: dict[str, dict] = {}

    # 1) Fluege -----------------------------------------------------------
    try:
        flights, fh = collect_flights(cfg, proxy=sec.flights_proxy)
    except Exception as exc:  # noqa: BLE001
        flights, fh = [], {"ok": False, "count": 0, "error": f"{type(exc).__name__}: {exc}"}
    health["flights"] = fh

    # 1a) Multi-City ueber echten Browser statt primp - primp bekommt fuer
    # diese Routen reproduzierbar keine Daten (siehe Modul-Docstring).
    try:
        mc_offers, mch = flights_multicity_browser.collect(cfg)
        flights += mc_offers
        health["flights"]["multicity_browser"] = mch
    except Exception as exc:  # noqa: BLE001
        health["flights"]["multicity_browser"] = {
            "ok": False, "count": 0, "error": f"{type(exc).__name__}: {exc}"}

    # 1b) Gruppen-/Split-Preis-Verifikation ueber echten Browser statt primp
    # (primp liefert fuer Mehrpersonen-Suchen oft nur einen Bruchteil der
    # echten Ergebnisse - siehe Modul-Docstring, kritischer Fund 13.09.26).
    # Mutiert `flights` in-place (group_check_unconfirmed-Flags + neue
    # group/split_*-Angebote).
    try:
        gh = flights_group_browser.collect(cfg, flights)
        health["flights"]["group_browser"] = gh
    except Exception as exc:  # noqa: BLE001
        health["flights"]["group_browser"] = {
            "ok": False, "group_checked": 0, "error": f"{type(exc).__name__}: {exc}"}

    constraint_summary = apply_constraints(flights, cfg)
    flights = dedup_flights(flights)
    health["flights"]["constraints"] = constraint_summary

    # 2) Hotels ---------------------------------------------------------
    try:
        hotels, hh = collect_hotels(cfg, proxy=sec.flights_proxy)
    except Exception as exc:  # noqa: BLE001
        hotels, hh = [], {"ok": False, "count": 0, "error": f"{type(exc).__name__}: {exc}"}
    health["hotels"] = hh

    # 3) Pauschalreisen --------------------------------------------------
    try:
        packages, ph = collect_packages(cfg)
    except Exception as exc:  # noqa: BLE001
        packages, ph = [], {"ok": False, "count": 0, "error": f"{type(exc).__name__}: {exc}"}
    health["packages"] = ph

    # 3a) ITA Matrix - reine Recherche-Referenz, NICHT buchbar, fliesst
    # deshalb NICHT in den Vergleich/Verdict ein (siehe Modul-Docstring).
    try:
        ita_matrix_rows, ih = flights_ita_matrix.collect(cfg)
    except Exception as exc:  # noqa: BLE001
        ita_matrix_rows, ih = [], {"ok": False, "count": 0, "error": f"{type(exc).__name__}: {exc}"}
    health["ita_matrix"] = ih

    # 3b) Manuell recherchierte Referenzpreise (CHECK24 & Co.) einmischen
    ref_hotels, ref_packages = _reference_offers(cfg)
    hotels += ref_hotels
    packages += ref_packages
    if ref_hotels or ref_packages:
        health.setdefault("hotels", {}).setdefault("count", 0)
        log.info("Referenzpreise eingemischt: %d Hotel, %d Pauschal",
                 len(ref_hotels), len(ref_packages))

    # 4) Vergleich ----------------------------------------------------
    verdict: Verdict = build_verdict(flights, hotels, packages, cfg)

    # 5) Persistenz -------------------------------------------------
    id_map: dict[int, FlightOfferRow] = {}
    for o in flights:
        row = _flight_row(run.id, o)
        session.add(row)
        id_map[id(o)] = row
    hotel_rows: dict[int, HotelOfferRow] = {}
    for o in hotels:
        row = _hotel_row(run.id, o)
        session.add(row)
        hotel_rows[id(o)] = row
    for o in packages:
        session.add(_package_row(run.id, o))
    session.flush()  # IDs vergeben

    # flight_out_id zeigt auf die gewaehlte zusammenhaengende Buchung
    # (round_trip oder multi_city); flight_ret_id wird nicht mehr benutzt.
    _fl_row = id_map.get(id(verdict.flight)) if verdict.flight else None

    snap = BestSnapshot(
        run_id=run.id, currency=cfg.trip.currency,
        flight_out_id=(_fl_row.id if _fl_row else None),
        flight_ret_id=None,
        flight_total=verdict.flight_total,
        hotel_id=(hotel_rows[id(verdict.hotel)].id if verdict.hotel and id(verdict.hotel) in hotel_rows else None),
        hotel_total=verdict.hotel_total,
        separate_total=verdict.separate_total,
        package_total=verdict.package_total,
        verdict=verdict.as_dict(),
    )
    session.add(snap)

    run.finished_at = datetime.now(timezone.utc)
    # ita_matrix ist eine reine Bonus-Recherchequelle (nicht buchbar, siehe
    # oben) - ein Fehlschlag dort soll den Gesamtstatus NICHT auf
    # partial/error ziehen, sonst waere praktisch jeder Lauf "partial".
    run.status = _overall_status({k: v for k, v in health.items() if k != "ita_matrix"})
    run.source_health = health
    run.ita_matrix = ita_matrix_rows
    run.notes = " | ".join(verdict.notes)
    session.commit()

    # 6) Alerts ------------------------------------------------------
    snapshot_values = {
        "flight_total": verdict.flight_total,
        "hotel_total": verdict.hotel_total,
        "separate_total": verdict.separate_total,
        "package_total": verdict.package_total,
    }
    alerts = maybe_alert(session, cfg, sec, snapshot_values, verdict.as_dict())

    session.close()
    log.info("=== CheckRun #%s fertig: status=%s ===", run.id, run.status)
    return {
        "run_id": run.id,
        "status": run.status,
        "health": health,
        "verdict": verdict.as_dict(),
        "alerts": alerts,
        "counts": {
            "flights": len(flights),
            "flights_kept": constraint_summary["kept"],
            "hotels_ok": sum(1 for h in hotels if h.ok),
            "packages_ok": sum(1 for p in packages if p.ok),
        },
    }
