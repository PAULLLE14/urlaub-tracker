from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import select

import app.collector as col
import app.database as db
from app import sync
from app.config import BookedFlight, Secrets, get_config
from app.models import BestSnapshot, CheckRun, HotelOfferRow
from app.offers import HotelOffer


def _fresh_db(monkeypatch, path):
    monkeypatch.setattr(db, "_url", lambda: f"sqlite:///{path.as_posix()}")
    monkeypatch.setattr(db, "_engine", None)
    monkeypatch.setattr(db, "_Session", None)
    db.init_db()


def _make_run(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path / "worker.db")
    cfg = get_config().model_copy(deep=True)
    cfg.trip.booked_flight = BookedFlight(price_total=7183.36, outbound_date=date(2027, 5, 14),
                                          return_date=date(2027, 5, 28))
    hotels = [HotelOffer(source=f"P{i} (Google)", ok=True, price_total=9000.0 + i, currency="EUR",
                         nights=13, rooms=4, guests=8,
                         raw={"checkin": "2027-05-15", "checkout": "2027-05-28"}) for i in range(3)]
    monkeypatch.setattr(col, "get_config", lambda: cfg)
    monkeypatch.setattr(col, "collect_hotels", lambda c, proxy=None: (hotels, {"ok": True, "count": 3}))
    monkeypatch.setattr(col, "maybe_alert", lambda *a, **k: [])
    return col.run_check("test")["run_id"]


def test_export_import_roundtrip_remaps_snapshot_ids(monkeypatch, tmp_path):
    run_id = _make_run(monkeypatch, tmp_path)
    s = db.get_session()
    payload = sync.export_run(s, run_id)
    s.close()

    # Empfaenger-DB mit Vorlauf, damit die IDs sicher abweichen
    _fresh_db(monkeypatch, tmp_path / "receiver.db")
    s = db.get_session()
    s.add(CheckRun(status="ok"))
    s.add(HotelOfferRow(run_id=1, source="alt"))
    s.commit()
    new_id = sync.import_run(s, payload)
    assert new_id == 2
    assert sync.import_run(s, payload) is None          # idempotent
    hotels = list(s.scalars(select(HotelOfferRow).where(HotelOfferRow.run_id == new_id)))
    snap = s.scalar(select(BestSnapshot).where(BestSnapshot.run_id == new_id))
    s.close()
    assert len(hotels) == 3
    assert snap.hotel_id in {h.id for h in hotels}       # auf neue ID umgebogen
    assert snap.hotel_total == 9000.0 and snap.flight_total == 7183.36


def test_ingest_endpoint_needs_token_and_receiver_mode(monkeypatch, tmp_path):
    run_id = _make_run(monkeypatch, tmp_path)
    s = db.get_session()
    payload = sync.export_run(s, run_id)
    s.close()
    _fresh_db(monkeypatch, tmp_path / "receiver.db")

    from app.main import app
    client = TestClient(app)
    monkeypatch.setattr(sync, "get_secrets", lambda: Secrets(sync_token="geheim"))

    assert client.post("/api/worker/ingest", json=payload).status_code == 401
    assert client.post("/api/worker/ingest", json=payload,
                       headers={"X-Sync-Token": "falsch"}).status_code == 401
    ok = client.post("/api/worker/ingest", json=payload, headers={"X-Sync-Token": "geheim"})
    assert ok.json()["imported"] is True

    # Manuell angefordert -> Worker erfaehrt es genau einmal beim naechsten Poll
    sync.request_check()
    h = {"X-Sync-Token": "geheim"}
    assert client.post("/api/worker/poll", json={}, headers=h).json() == {"requested": True}
    assert client.post("/api/worker/poll", json={}, headers=h).json() == {"requested": False}

    # Ohne SYNC_TOKEN ist der Empfang aus
    monkeypatch.setattr(sync, "get_secrets", lambda: Secrets())
    assert client.post("/api/worker/ingest", json=payload).status_code == 404


def test_receiver_checks_alerts_after_import(monkeypatch, tmp_path):
    import app.notify as notify

    run_id = _make_run(monkeypatch, tmp_path)
    s = db.get_session()
    payload = sync.export_run(s, run_id)
    s.close()
    _fresh_db(monkeypatch, tmp_path / "receiver.db")
    seen = {}
    monkeypatch.setattr(notify, "maybe_alert",
                        lambda session, cfg, sec, values, verdict: seen.update(values=values) or ["ALERT"])
    s = db.get_session()
    new_id = sync.import_run(s, payload)
    assert sync.alert_for_run(s, new_id) == ["ALERT"]
    s.close()
    assert seen["values"]["hotel_total"] == 9000.0
