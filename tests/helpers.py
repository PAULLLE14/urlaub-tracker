"""Baukasten fuer FlightOffer-Testdaten."""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.geo import get_airport
from app.offers import FlightOffer, Segment


def seg(frm: str, to: str, dep: str, arr: str, plane: str = "", airline: str = "") -> Segment:
    dtz = get_airport(frm).tz
    atz = get_airport(to).tz
    d = datetime.fromisoformat(dep).replace(tzinfo=ZoneInfo(dtz))
    a = datetime.fromisoformat(arr).replace(tzinfo=ZoneInfo(atz))
    return Segment(
        from_airport=frm, to_airport=to, departure=d, arrival=a,
        departure_tz=dtz, arrival_tz=atz,
        duration_minutes=int((a - d).total_seconds() // 60),
        airline=airline, plane_type=plane,
    )


def offer(direction: str, segments: list[Segment], price_total: float = 8000.0,
          search_date: str = "2027-05-14", persons: int = 8,
          trip_type: str = "one_way", return_date: str | None = None) -> FlightOffer:
    return FlightOffer(
        source="test", direction=direction, trip_type=trip_type,
        return_date=date.fromisoformat(return_date) if return_date else None,
        origin=segments[0].from_airport, destination=segments[-1].to_airport,
        search_date=date.fromisoformat(search_date),
        price_total=price_total, price_per_person=price_total / persons,
        currency="EUR", airlines=[s.airline for s in segments if s.airline],
        segments=segments,
    )


def rt_offer(origin: str, out_segments: list[Segment], price_total: float,
             out_date: str = "2027-05-14", ret_date: str = "2027-05-28",
             persons: int = 8) -> FlightOffer:
    """Round-Trip-Angebot: Preis = Gesamt Hin+Rueck, Segmente = nur Hinflug."""
    return offer("round_trip", out_segments, price_total=price_total,
                 search_date=out_date, persons=persons,
                 trip_type="round_trip", return_date=ret_date)


def mc_offer(out_segments: list[Segment], ret_segments: list[Segment],
             price_total: float, out_date: str = "2027-05-14",
             ret_date: str = "2027-05-28", persons: int = 8) -> FlightOffer:
    """Multi-City-Angebot: Hinflug-Airport != Rueckflug-Airport, EIN Ticket."""
    o = offer("multi_city", out_segments, price_total=price_total,
              search_date=out_date, persons=persons,
              trip_type="multi_city", return_date=ret_date)
    o.return_segments = ret_segments
    o.destination = ret_segments[-1].to_airport if ret_segments else o.destination
    return o
