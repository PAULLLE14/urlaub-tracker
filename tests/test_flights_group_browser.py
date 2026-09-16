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


def _no_network_ladder(monkeypatch, price=None):
    """Preis-Leiter (Roadmap 2.1) macht sonst einen echten primp-Request,
    und die Buchungsoptionen-Abfrage (Nutzer-Fund 16.09.26) einen echten
    Playwright-Durchklick - in Tests immer beide mocken, damit sie nicht
    vom Netzwerk/Browser abhaengen."""
    monkeypatch.setattr(gb, "cheapest_price_no_bag", lambda cfg, fetcher, origin, out_d, ret_d: price)

    async def _no_booking_options(cfg, origin, out_d, ret_d, pax):
        return None
    monkeypatch.setattr(gb, "cheapest_round_trip_booking_options", _no_booking_options)


def test_group_offer_uses_real_cheapest_cards_own_data(monkeypatch):
    # A1-Regression: das guenstigste 8-Pax-Angebot MUSS seine eigenen
    # Airlines/Segmente bekommen (Qatar/DOH/BKK), nicht die des 1-Pax-
    # Schaetzangebots (Lufthansa, direkt) - vorher wurde nur der Preis
    # uebernommen und mit fremden Flugdaten kombiniert.
    monkeypatch.setattr(gb, "cheapest_round_trip_cards",
                        _fake_cards({8: ([CARD_QATAR, CARD_CONDOR], "https://example.test/8")}))
    _no_network_ladder(monkeypatch)

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
    _no_network_ladder(monkeypatch)
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
    _no_network_ladder(monkeypatch)
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
    _no_network_ladder(monkeypatch)
    cfg = get_config()
    offers = [_est(price=6000.0)]
    health = asyncio.run(gb.verify(cfg, offers))

    split_offers = [o for o in offers if o.pax_mode == "split_4_4"]
    assert len(split_offers) == 1
    assert split_offers[0].price_total == 6400.0  # 2 x 3200
    assert "UNTERGRENZE" in split_offers[0].price_confidence


def test_price_ladder_filled_for_top_combo(monkeypatch):
    # Roadmap Runde 2, Punkt 2.1: die guenstigste (Top-5) Kombination bekommt
    # zusaetzlich zum bestaetigten 8-Pax-Preis einen 1-Pax-ohne-Gepaeck-Preis,
    # damit im Dashboard sichtbar wird, was vom Preisunterschied zu einer
    # schnellen manuellen Suche am Gepaeck liegt.
    card8 = {**CARD_QATAR, "price": 7939.0}
    monkeypatch.setattr(gb, "cheapest_round_trip_cards",
                        _fake_cards({8: ([card8], "https://example.test/8")}))
    _no_network_ladder(monkeypatch, price=650.0)

    cfg = get_config()
    offers = [_est(price=8000.0)]
    asyncio.run(gb.verify(cfg, offers))

    g = next(o for o in offers if o.pax_mode == "group")
    assert g.price_ladder["1_pax_ohne_gepaeck"] == 650.0
    assert g.price_ladder["1_pax_mit_gepaeck"] == 1000.0  # _est() Preis 8000 / 8
    assert g.price_ladder["8_pax"] == round(7939.0 / 8, 2)


def test_price_ladder_uses_confirmed_top5_not_estimate_order(monkeypatch):
    # Roadmap Runde 3 (externe Review): Top-5 fuer die Preis-Leiter muessen
    # nach dem ECHTEN bestaetigten 8-Pax-Preis gewaehlt werden, nicht nach
    # der 1-Pax-Schaetzung - sonst haengt die Leiter an Kombis, die im
    # tatsaechlichen Ranking gar nicht vorne liegen.
    combos = [("STR", "2027-05-28"), ("STR", "2027-05-29"), ("MUC", "2027-05-28"),
             ("MUC", "2027-05-29"), ("FRA", "2027-05-28"), ("FRA", "2027-05-29")]
    # Schaetzpreise steigend ueber die Liste (combos[0] hat die GUENSTIGSTE
    # Schaetzung), bestaetigte p.P.-Preise GENAU UMGEKEHRT (combos[0] hat den
    # TEUERSTEN bestaetigten Preis, faellt also aus den Top-5 raus).
    estimate_price = {i: 6000.0 + i * 100 for i in range(6)}
    confirmed_pp = {i: 900.0 - i * 50 for i in range(6)}

    async def fake_cards(cfg, origin, out_d, ret_d, pax):
        for i, (o, rd) in enumerate(combos):
            if o == origin and rd == ret_d.isoformat():
                card = {**CARD_QATAR, "origin": origin, "price": confirmed_pp[i] * 8}
                return ([card], f"https://example.test/{origin}/{rd}")
        return ([], "")

    monkeypatch.setattr(gb, "cheapest_round_trip_cards", fake_cards)
    _no_network_ladder(monkeypatch, price=500.0)

    cfg = get_config()
    offers = [
        rt_offer(o, [seg(o, "USM", "2027-05-14T17:00", "2027-05-15T09:00")],
                 price_total=estimate_price[i], ret_date=rd)
        for i, (o, rd) in enumerate(combos)
    ]
    asyncio.run(gb.verify(cfg, offers))

    group_offers = {(o.origin, o.return_date.isoformat()): o
                    for o in offers if o.pax_mode == "group"}
    # combos[0] (billigste SCHAETZUNG, teuerster bestaetigter Preis) darf
    # KEINE Preis-Leiter bekommen haben.
    assert "1_pax_ohne_gepaeck" not in group_offers[combos[0]].price_ladder
    # combos[1..5] (die 5 GUENSTIGSTEN bestaetigten Preise) muessen sie haben.
    for combo in combos[1:]:
        assert "1_pax_ohne_gepaeck" in group_offers[combo].price_ladder


def test_booking_options_attached_to_cheapest_confirmed_offer(monkeypatch):
    # Nutzer-Fund 16.09.26: die Ergebniskarte zeigt nur den Airline-Preis -
    # fuer die tatsaechlich guenstigste bestaetigte Kombi wird zusaetzlich
    # Googles Buchungsoptionen-Seite abgefragt (Drittanbieter oft guenstiger).
    card8 = {**CARD_QATAR, "price": 7352.0}
    monkeypatch.setattr(gb, "cheapest_round_trip_cards",
                        _fake_cards({8: ([card8], "https://example.test/8")}))
    _no_network_ladder(monkeypatch, price=650.0)

    async def fake_booking_options(cfg, origin, out_d, ret_d, pax):
        return {"lowest_total": 7080.0, "options": [
            {"provider": "lastminute.com", "price": 7080.0, "is_airline": False},
            {"provider": "Qatar Airways", "price": 7352.0, "is_airline": True},
        ]}
    monkeypatch.setattr(gb, "cheapest_round_trip_booking_options", fake_booking_options)

    cfg = get_config()
    offers = [_est(price=8000.0)]
    asyncio.run(gb.verify(cfg, offers))

    g = next(o for o in offers if o.pax_mode == "group")
    assert g.booking_options[0]["provider"] == "lastminute.com"
    assert g.booking_options[0]["price"] == 7080.0


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
