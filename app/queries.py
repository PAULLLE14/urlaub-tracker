"""Lesezugriffe fuers Dashboard (reine SELECTs, keine Schreiblogik)."""
from __future__ import annotations

from typing import Any

from sqlalchemy import desc, select

from .config import Config
from .logic.trends import compute_trends, history_series
from .models import (
    BestSnapshot,
    CheckRun,
    FlightOfferRow,
    HotelOfferRow,
    PackageOfferRow,
)


def latest_run(session) -> CheckRun | None:
    return session.scalars(
        select(CheckRun).where(CheckRun.status != "running")
        .order_by(desc(CheckRun.started_at)).limit(1)
    ).first()


def _run_id(session, run: str | int | None) -> int | None:
    if run in (None, "latest", ""):
        r = latest_run(session)
        return r.id if r else None
    return int(run)


def status_payload(session, cfg: Config, scheduler_info: dict | None = None) -> dict:
    r = latest_run(session)
    total_runs = session.scalar(select(CheckRun.id).order_by(desc(CheckRun.id)).limit(1)) or 0
    return {
        "trip": cfg.trip.label,
        "currency": cfg.trip.currency,
        "employee_discounts": cfg.trip.employee_discounts,
        "departure_date": cfg.trip.outbound_dates[0].isoformat() if cfg.trip.outbound_dates else None,
        "hotel_checkin": cfg.trip.hotel_checkin.isoformat(),
        "hotel_checkout": cfg.trip.hotel_checkout.isoformat(),
        "last_run": None if not r else {
            "id": r.id,
            "started_at": r.started_at.isoformat(),
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "status": r.status,
            "trigger": r.trigger,
            "notes": r.notes,
            "source_health": r.source_health,
        },
        "total_runs": total_runs,
        "scheduler": scheduler_info or {},
    }


def _route_of(segments: list) -> list[str]:
    if not segments:
        return []
    codes = [segments[0].get("from", "")]
    codes += [s.get("to", "") for s in segments]
    return [c for c in codes if c]


def flight_offer_dict(f: FlightOfferRow) -> dict[str, Any]:
    ret_segs = f.return_segments or []
    return {
        "id": f.id, "source": f.source, "direction": f.direction,
        "trip_type": f.trip_type, "return_date": f.return_date,
        "origin": f.origin, "destination": f.destination,
        "search_date": f.search_date,
        "route": _route_of(f.segments) or [f.origin, f.destination],
        "return_route": _route_of(ret_segs),
        "price_total": f.price_total, "price_per_person": f.price_per_person,
        "currency": f.currency, "airlines": f.airlines,
        "segments": f.segments, "return_segments": ret_segs,
        "stops": f.stops, "layover_airports": f.layover_airports,
        "layover_minutes": f.layover_minutes,
        "total_duration_minutes": f.total_duration_minutes,
        "carbon_grams": f.carbon_grams, "deep_link": f.deep_link,
        "captured_at": f.captured_at.isoformat() if f.captured_at else None,
        "excluded": f.excluded, "exclude_reason": f.exclude_reason,
        "departure": (f.segments[0]["departure"] if f.segments else None),
        "pax_mode": f.pax_mode, "price_confidence": f.price_confidence,
        "segment_times_approximate": f.segment_times_approximate,
        "price_ladder": f.price_ladder,
        "booking_options": f.booking_options,
    }


def flights_table(session, cfg: Config, *, run: str | int | None = None,
                  direction: str | None = None, origin: str | None = None,
                  airline: str | None = None, max_stops: int | None = None,
                  include_excluded: bool = True, sort: str = "price",
                  descending: bool = False) -> dict:
    rid = _run_id(session, run)
    if rid is None:
        return {"run_id": None, "rows": []}
    q = select(FlightOfferRow).where(FlightOfferRow.run_id == rid)
    # Diese Tabelle zeigt NIE Einzelrichtungs-Tickets, unabhaengig vom
    # Filter-Dropdown - die one_way-Legs existieren nur fuer die
    # Rueckflug-Referenz (siehe combine._find_return_reference) und duerfen
    # hier nicht als eigene Buchungsoption auftauchen.
    q = q.where(FlightOfferRow.trip_type.in_(("round_trip", "multi_city")))
    if direction:
        q = q.where(FlightOfferRow.trip_type == direction)
    if origin:
        q = q.where(FlightOfferRow.origin == origin.upper())
    if not include_excluded:
        q = q.where(FlightOfferRow.excluded.is_(False))
    rows = [flight_offer_dict(f) for f in session.scalars(q)]

    if airline:
        a = airline.lower()
        rows = [r for r in rows if any(a in x.lower() for x in r["airlines"])]
    if max_stops is not None:
        rows = [r for r in rows if r["stops"] <= max_stops]

    # Zweistufig sortieren: zuerst nach der gewaehlten Spalte (mit Richtung),
    # dann stabil nach "ausgefiltert" - so bleiben excl. Zeilen immer unten,
    # unabhaengig von der Sortierrichtung.
    keyfn = {
        "price": lambda r: r["price_total"],
        "price_per_person": lambda r: r["price_per_person"],
        "stops": lambda r: (r["stops"], r["price_total"]),
        "duration": lambda r: r["total_duration_minutes"],
        "departure": lambda r: r["departure"] or "",
        "origin": lambda r: (r["origin"], r["price_total"]),
        "airline": lambda r: ((r["airlines"] or [""])[0], r["price_total"]),
        # Nutzerwunsch 16.09.26: "sortieren nach 3. Anbieter Preis" - der
        # guenstigste Buchungsoptionen-Preis (Airline oder Drittanbieter,
        # siehe flights_browser_search.cheapest_round_trip_booking_options),
        # Zeilen ohne Buchungsoptionen fallen auf price_total zurueck (keine
        # Bevorzugung, nur ein sinnvoller Default).
        "booking_option_price": lambda r: min(
            (o["price"] for o in r["booking_options"]), default=r["price_total"]),
    }.get(sort, lambda r: r["price_total"])
    rows.sort(key=keyfn, reverse=descending)
    rows.sort(key=lambda r: r["excluded"])
    return {"run_id": rid, "rows": rows}


def ita_matrix_table(session, run: str | int | None = None) -> dict:
    """ITA Matrix - reine Recherche-Referenz, siehe sources/flights_ita_matrix.py.
    Bewusst als eigene Kategorie (nicht Teil von flights_table)."""
    rid = _run_id(session, run)
    if rid is None:
        return {"run_id": None, "rows": []}
    run_obj = session.get(CheckRun, rid)
    rows = list((run_obj.ita_matrix if run_obj else []) or [])
    rows.sort(key=lambda r: (not r.get("ok"), r.get("price_total") or 9e18))
    return {"run_id": rid, "rows": rows}


def hotels_table(session, run: str | int | None = None) -> dict:
    rid = _run_id(session, run)
    if rid is None:
        return {"run_id": None, "rows": []}
    rows = []
    for h in session.scalars(select(HotelOfferRow).where(HotelOfferRow.run_id == rid)):
        rows.append({
            "id": h.id, "source": h.source, "ok": h.ok,
            "price_total": h.price_total, "per_night": h.per_night,
            "currency": h.currency, "room_desc": h.room_desc,
            "nights": h.nights, "rooms": h.rooms, "guests": h.guests,
            "deep_link": h.deep_link, "error": h.error, "raw": h.raw, "is_reference": h.is_reference,
            "captured_at": h.captured_at.isoformat() if h.captured_at else None,
        })
    rows.sort(key=lambda r: (not r["ok"], r["price_total"] or 9e18))
    return {"run_id": rid, "rows": rows}


def packages_table(session, run: str | int | None = None) -> dict:
    rid = _run_id(session, run)
    if rid is None:
        return {"run_id": None, "rows": []}
    rows = []
    for p in session.scalars(select(PackageOfferRow).where(PackageOfferRow.run_id == rid)):
        rows.append({
            "id": p.id, "source": p.source, "ok": p.ok,
            "price_total": p.price_total, "currency": p.currency,
            "operator": p.operator, "hotel_name": p.hotel_name,
            "dep_airport": p.dep_airport, "board": p.board,
            "nights": p.nights, "persons": p.persons,
            "deep_link": p.deep_link, "error": p.error, "raw": p.raw, "is_reference": p.is_reference,
            "captured_at": p.captured_at.isoformat() if p.captured_at else None,
        })
    rows.sort(key=lambda r: (not r["ok"], r["price_total"] or 9e18))
    return {"run_id": rid, "rows": rows}


def summary_payload(session, cfg: Config) -> dict:
    r = latest_run(session)
    snap = None
    if r:
        snap = session.scalars(
            select(BestSnapshot).where(BestSnapshot.run_id == r.id)
        ).first()
    trends = compute_trends(session, cfg)
    fl = _row(session, snap.flight_out_id) if snap else None
    return {
        "run_id": r.id if r else None,
        "captured_at": snap.created_at.isoformat() if snap else None,
        "currency": cfg.trip.currency,
        "persons": cfg.trip.persons,
        "verdict": snap.verdict if snap else {},
        "totals": None if not snap else {
            "flight_total": snap.flight_total,
            "hotel_total": snap.hotel_total,
            "separate_total": snap.separate_total,
            "package_total": snap.package_total,
        },
        "trends": trends,
        "flight_source": (snap.verdict or {}).get("flight_source") if snap else None,
        "flight": flight_offer_dict(fl) if fl else None,
    }


def _row(session, fid: int | None) -> FlightOfferRow | None:
    if not fid:
        return None
    return session.get(FlightOfferRow, fid)


def history_payload(session, cfg: Config) -> dict:
    return history_series(session, cfg)


def runs_list(session, limit: int = 30) -> list[dict]:
    out = []
    for r in session.scalars(select(CheckRun).order_by(desc(CheckRun.id)).limit(limit)):
        out.append({
            "id": r.id, "started_at": r.started_at.isoformat(),
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "status": r.status, "trigger": r.trigger, "notes": r.notes,
        })
    return out
