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


def test_price_alert_mail_uses_same_design(monkeypatch, tmp_path):
    test_digest_describes_price_and_combination(monkeypatch, tmp_path)
    cfg = get_config()
    s = db.get_session()
    subject, html, text = build_digest(s, cfg, alert={"price": 8420.0, "limit": 8500.0})
    s.close()
    assert "Preisalarm" in subject and "8.420 €" in subject
    assert "80 € unter deiner Schwelle von 8.500 €" in html
    assert "Preisalarm · " in html and "Guten Morgen" not in html


def test_digest_skips_degraded_latest_run(monkeypatch, tmp_path):
    from datetime import datetime, timedelta

    from app.models import BestSnapshot, CheckRun, HotelOfferRow

    monkeypatch.setattr(db, "_url", lambda: f"sqlite:///{(tmp_path / 'q.db').as_posix()}")
    monkeypatch.setattr(db, "_engine", None)
    monkeypatch.setattr(db, "_Session", None)
    db.init_db()
    s = db.get_session()
    t0 = datetime(2026, 11, 10, 8, 0)

    def make(i, providers, total):
        r = CheckRun(started_at=t0 + timedelta(hours=6 * i), status="ok")
        s.add(r)
        s.flush()
        for n in range(providers):
            s.add(HotelOfferRow(run_id=r.id, source=f"P{n}", ok=True, price_total=total + n))
        s.add(BestSnapshot(run_id=r.id, hotel_total=total, flight_total=7183.36,
                           separate_total=7183.36 + total, created_at=r.started_at,
                           verdict={"hotel": {"source": "P0", "checkin": "2027-05-15",
                                              "checkout": "2027-05-28"}}))
        return r

    make(0, 20, 9000.0)      # vollstaendig
    make(1, 9, 9788.0)       # gedrosselt - darf die Mail nicht bestimmen
    s.commit()
    subject, html, text = build_digest(s, get_config())
    s.close()
    assert "9.000 €" in subject and "9.788" not in html
    assert "Preisstand: 10.11. 09:00 Uhr" in text      # 08:00 UTC = 09:00 Berlin
