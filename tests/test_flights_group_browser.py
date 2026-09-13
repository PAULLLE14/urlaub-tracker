import asyncio

import app.sources.flights_group_browser as gb
from app.config import get_config
from tests.helpers import rt_offer, seg

CARD_QATAR = {
    "dep_time": "17:15", "airlines": ["Qatar Airways", "Bangkok Airways"],
    "duration_min": 18 * 60 + 15, "origin": "FRA", "dest": "USM",
    "stops": 2, "layovers": ["DOH", "BKK"], "price": 7939.0,
}
CARD_CONDOR = {
    "dep_time": "21:10", "airlines": ["Condor", "Bangkok Airways"],
    "duration_min": 14 * 60 + 20, "origin": "FRA", "dest": "USM",
    "stops": 1, "layovers": ["BKK"], "price": 9200.0,
}


def _fake_cards(mapping):
    """mapping: {pax: (cards, deep_link)}"""
    async def fake(cfg, origin, out_d, ret_d, pax):
        return mapping.get(pax, ([], f"https://example.test/{pax}"))
    return fake


def _est(price=8000.0):
    return rt_offer("FRA", [
        seg("FRA", "USM", "2027-05-14T17:00", "2027-05-15T09:00", airline="Lufthansa"),
    ], price_total=price)


def test_group_offer_uses_real_cheapest_cards_own_data(monkeypatch):
    # A1-Regression: das guenstigste 8-Pax-Angebot MUSS seine eigenen
    # Airlines/Segmente bekommen (Qatar/DOH/BKK), nicht die des 1-Pax-
    # Schaetzangebots (Lufthansa, direkt) - vorher wurde nur der Preis
    # uebernommen und mit fremden Flugdaten kombiniert.
    monkeypatch.setattr(gb, "cheapest_round_trip_cards",
                        _fake_cards({8: ([CARD_QATAR, CARD_CONDOR], "https://example.test/8")}))

    cfg = get_config()
    offers = [_est()]
    health = asyncio.run(gb.verify(cfg, offers))

    group_offers = [o for o in offers if o.pax_mode == "group"]
    assert len(group_offers) == 1
    g = group_offers[0]
    assert g.price_total == 7939.0
    assert g.airlines == ["Qatar Airways", "Bangkok Airways"]
    assert g.segments[0].from_airport == "FRA"
    assert [s.to_airport for s in g.segments] == ["DOH", "BKK", "USM"]
    assert g.segment_times_approximate is True
    assert health["group_checked"] == 1


def test_split_skipped_when_bucket_covers_all_eight(monkeypatch):
    # Preis(8)/8 (992) liegt nah an Preis(4)/4 (990) -> derselbe Bucket
    # deckt vermutlich alle 8 Personen, kein Split-Angebot noetig.
    card8 = {**CARD_QATAR, "price": 7939.0}   # /8 = 992.375
    card4 = {**CARD_QATAR, "price": 3960.0}   # /4 = 990.0
    monkeypatch.setattr(gb, "cheapest_round_trip_cards", _fake_cards({
        8: ([card8], "https://example.test/8"),
        4: ([card4], "https://example.test/4"),
    }))
    cfg = get_config()
    offers = [_est(price=7939.0 * 1.0)]  # 1-Pax-Schaetzung nah am 8er-Preis -> Split-Check laeuft gar nicht erst
    health = asyncio.run(gb.verify(cfg, offers))
    assert not [o for o in offers if o.pax_mode.startswith("split_")]
    assert health["split_checked"] == 0


def test_split_estimate_marked_as_upper_bound_not_confirmed(monkeypatch):
    # Preis(8)/8 deutlich hoeher als Preis(4)/4 -> knapper Bucket vermutet.
    # Split-Angebot = 4x billig + 4x zum vollen 8er-Preis, NIE "bestaetigt".
    card8 = {**CARD_CONDOR, "price": 9200.0}   # /8 = 1150.0
    card4 = {**CARD_QATAR, "price": 3200.0}    # /4 = 800.0
    monkeypatch.setattr(gb, "cheapest_round_trip_cards", _fake_cards({
        8: ([card8], "https://example.test/8"),
        4: ([card4], "https://example.test/4"),
    }))
    cfg = get_config()
    # 1-Pax-Schaetzung weit unter dem 8er-Preis/Pers., damit der
    # Split-Check ueberhaupt ausgeloest wird (siehe _SPLIT_WORTHWHILE_RATIO).
    offers = [_est(price=6000.0)]
    health = asyncio.run(gb.verify(cfg, offers))

    split_offers = [o for o in offers if o.pax_mode == "split_4_4"]
    assert len(split_offers) == 1
    s = split_offers[0]
    expected = 4 * 800.0 + 4 * 1150.0
    assert s.price_total == expected
    assert s.price_confidence  # nie leer fuer split_4_4
    assert "Schaetzung" in s.price_confidence or "chätzung" in s.price_confidence
    assert health["split_checked"] == 1


def test_split_falls_back_to_lower_bound_when_no_group_price(monkeypatch):
    # Kein 8er-Preis lesbar -> nur die alte Preis-UNTERGRENZE (2x Preis(4)),
    # aber explizit als Untergrenze markiert statt als bestaetigt.
    card4 = {**CARD_QATAR, "price": 3200.0}
    monkeypatch.setattr(gb, "cheapest_round_trip_cards", _fake_cards({
        4: ([card4], "https://example.test/4"),
    }))
    cfg = get_config()
    offers = [_est(price=6000.0)]
    health = asyncio.run(gb.verify(cfg, offers))

    split_offers = [o for o in offers if o.pax_mode == "split_4_4"]
    assert len(split_offers) == 1
    assert split_offers[0].price_total == 6400.0  # 2 x 3200
    assert "UNTERGRENZE" in split_offers[0].price_confidence


def test_multicity_gets_real_group_check(monkeypatch):
    # A3-Regression: eine Multi-City-1-Pax-Hochrechnung darf nicht mehr
    # unbestaetigt bleiben - der guenstigste Kandidat bekommt einen echten
    # 8-Pax-Check ueber flights_multicity_browser._search_one.
    from app.offers import FlightOffer, Segment

    async def fake_mc_search(cfg, origin, dest, out_d, ret_d, pax):
        assert pax == cfg.trip.persons
        seg_o = Segment(from_airport=origin, to_airport="USM",
                        departure=__import__("datetime").datetime.fromisoformat("2027-05-14T17:00:00+02:00"),
                        arrival=__import__("datetime").datetime.fromisoformat("2027-05-15T09:00:00+07:00"),
                        departure_tz="Europe/Berlin", arrival_tz="Asia/Bangkok", duration_minutes=600)
        return FlightOffer(source="test", direction="multi_city", trip_type="multi_city",
                           origin=origin, destination=dest, search_date=out_d, return_date=ret_d,
                           price_total=9000.0, price_per_person=1125.0, currency="EUR",
                           segments=[seg_o], pax_mode="group")

    monkeypatch.setattr(gb, "_mc_search_one", fake_mc_search)
    monkeypatch.setattr(gb, "cheapest_round_trip_cards", _fake_cards({}))

    cfg = get_config()
    from tests.helpers import mc_offer
    mc = mc_offer(
        [seg("STR", "DOH", "2027-05-14T16:00", "2027-05-14T23:30")],
        [seg("USM", "MUC", "2027-05-28T09:00", "2027-05-28T19:30")],
        price_total=8500.0,
    )
    offers = [mc]
    health = asyncio.run(gb.verify(cfg, offers))

    confirmed = [o for o in offers if o.trip_type == "multi_city" and o.pax_mode == "group"]
    assert len(confirmed) == 1
    assert confirmed[0].price_total == 9000.0
    assert health["group_checked"] >= 1
