from datetime import date

import app.collector as col
import app.database as db
from app.config import BookedFlight, get_config
from app.digest import build_digest, eur
from app.offers import HotelOffer


def test_eur_formatting():
    assert eur(8962) == "8.962 €" and eur(7183.36, 2) == "7.183,36 €" and eur(None) == "–"


def test_digest_describes_price_and_combination(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "_url", lambda: f"sqlite:///{(tmp_path / 'd.db').as_posix()}")
    monkeypatch.setattr(db, "_engine", None)
    monkeypatch.setattr(db, "_Session", None)
    cfg = get_config().model_copy(deep=True)
    cfg.trip.booked_flight = BookedFlight(price_total=7183.36, outbound_date=date(2027, 5, 14),
                                          return_date=date(2027, 5, 28))
    h = HotelOffer(source="Mix: A (2×3er) + B (1×2er)", ok=True, price_total=8962.0, currency="EUR",
                   nights=13, rooms=3, guests=8, deep_link="https://x.test/a",
                   raw={"checkin": "2027-05-15", "checkout": "2027-05-28",
                        "room_split": {"3": 2, "2": 1},
                        "per_room_size": {"3": {"cheapest": 3240.0, "provider": "A", "url": "u"},
                                          "2": {"cheapest": 2482.0, "provider": "B", "url": "u"}}})
    monkeypatch.setattr(col, "get_config", lambda: cfg)
    monkeypatch.setattr(col, "collect_hotels", lambda c, proxy=None: ([h], {"ok": True, "count": 1}))
    monkeypatch.setattr(col, "maybe_alert", lambda *a, **k: [])
    col.run_check("test")

    s = db.get_session()
    subject, html, text = build_digest(s, cfg)
    s.close()
    assert "8.962 €" in subject and "15. Mai" in subject
    assert "2 × 3er-Zimmer bei A" in text and "1 × 2er-Zimmer bei B" in text
    assert "1.120 €" in html and "<html" in html
