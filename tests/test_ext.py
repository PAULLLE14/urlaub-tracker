from datetime import date

from fastapi.testclient import TestClient

import app.api as api_mod
import app.database as db
from app.config import Secrets, get_config
from app.offers import HotelOffer
from app.sources.hotels.crossmix import cross_mix

CI, CO = date(2027, 5, 15), date(2027, 5, 28)
BODY = {"checkin": "2027-05-15", "checkout": "2027-05-28", "adults": 2,
        "providers": [{"name": "Agoda", "per_night": 184, "total": 2392, "url": "https://x.test/a"},
                      {"name": "Quark", "per_night": 5, "total": 10, "url": ""},          # unplausibel
                      {"name": "Vio.com", "per_night": 196, "total": 2547, "url": ""}]}


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "_url", lambda: f"sqlite:///{(tmp_path / 'e.db').as_posix()}")
    monkeypatch.setattr(db, "_engine", None)
    monkeypatch.setattr(db, "_Session", None)
    db.init_db()
    monkeypatch.setattr(api_mod, "get_secrets", lambda: Secrets(ext_token="exttok", sync_token="synctok"))
    from app.main import app
    return TestClient(app)


def test_ext_ingest_needs_token_validates_and_serves_worker(monkeypatch, tmp_path):
    c = _client(monkeypatch, tmp_path)
    assert c.post("/api/ext/ingest", json=BODY).status_code == 401
    ok = c.post("/api/ext/ingest", json=BODY, headers={"X-Ext-Token": "exttok"}).json()
    assert ok == {"stored": True, "providers": 2}
    wrong = dict(BODY, checkin="2027-06-01")
    assert c.post("/api/ext/ingest", json=wrong, headers={"X-Ext-Token": "exttok"}).json()["stored"] is False
    # Worker holt die Messung nur mit dem Sync-Token
    assert c.get("/api/ext/latest").status_code == 401
    import app.sync as sync_mod
    monkeypatch.setattr(sync_mod, "get_secrets", lambda: Secrets(sync_token="synctok"))
    got = c.get("/api/ext/latest", headers={"X-Sync-Token": "synctok"}).json()["measurements"]
    assert len(got) == 1 and got[0]["adults"] == 2 and [p["name"] for p in got[0]["providers"]] == ["Agoda", "Vio.com"]


def test_cross_mix_uses_browser_prices():
    cfg = get_config()
    raw = {"checkin": "2027-05-15", "checkout": "2027-05-28"}
    google = HotelOffer(source="X (Google)", ok=True, price_total=9500.0, currency="EUR", nights=13,
                        raw={**raw, "per_room_size": {"3": {"cheapest": 3387.0, "url": "g3", "provider": "Super.com"},
                                                      "2": {"cheapest": 2547.0, "url": "g2", "provider": "Vio.com"}}})
    browser = [{"checkin": "2027-05-15", "checkout": "2027-05-28", "adults": 2,
                "providers": [{"name": "Agoda", "total": 2392.0, "url": "https://x.test/a"}]}]
    mix = cross_mix(cfg, [google], CI, CO, browser=browser)
    assert mix is not None and mix.price_total == 2 * 3387.0 + 2392.0
    assert "Agoda (Browser) (1×2er)" in mix.source
