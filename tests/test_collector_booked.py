from datetime import date

import app.collector as col
import app.database as db
from app.config import BookedFlight, get_config
from app.offers import HotelOffer


def test_run_check_with_booked_flight_completes_and_returns_counts(monkeypatch, tmp_path):
    # Regression 09.11.26: bei gebuchtem Flug war constraint_summary nicht
    # definiert -> jeder Lauf endete nach dem Speichern mit UnboundLocalError
    # ("Geplanter Check fehlgeschlagen").
    monkeypatch.setattr(db, "_url", lambda: f"sqlite:///{(tmp_path / 't.db').as_posix()}")
    monkeypatch.setattr(db, "_engine", None)
    monkeypatch.setattr(db, "_Session", None)

    cfg = get_config().model_copy(deep=True)
    cfg.trip.booked_flight = BookedFlight(price_total=7183.36, outbound_date=date(2027, 5, 14),
                                          return_date=date(2027, 5, 28))
    hotel = HotelOffer(source="Test (Google)", ok=True, price_total=9000.0, currency="EUR",
                       nights=13, rooms=4, guests=8,
                       raw={"checkin": "2027-05-15", "checkout": "2027-05-28"})
    monkeypatch.setattr(col, "get_config", lambda: cfg)
    monkeypatch.setattr(col, "collect_hotels", lambda c, proxy=None: ([hotel], {"ok": True, "count": 1}))
    monkeypatch.setattr(col, "maybe_alert", lambda *a, **k: [])

    result = col.run_check("test")

    assert result["status"] in ("ok", "partial")
    assert result["counts"]["flights"] == 1 and result["counts"]["flights_kept"] == 1
    assert result["verdict"]["flight_total"] == 7183.36
    assert result["verdict"]["hotel_total"] == 9000.0
