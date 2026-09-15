from app.config import get_config
from app.logic.combine import build_verdict
from app.offers import HotelOffer, PackageOffer
from tests.helpers import mc_offer, offer, rt_offer, seg


def _rt(price, origin="FRA"):
    return rt_offer(origin, [
        seg(origin, "DOH", "2027-05-14T16:00", "2027-05-14T23:30"),
        seg("DOH", "USM", "2027-05-15T02:00", "2027-05-15T11:30"),
    ], price_total=price)


def _mc(price, out_origin="STR", ret_dest="MUC"):
    out = [
        seg(out_origin, "DOH", "2027-05-14T16:00", "2027-05-14T23:30"),
        seg("DOH", "USM", "2027-05-15T02:00", "2027-05-15T11:30"),
    ]
    ret = [
        seg("USM", "BKK", "2027-05-28T09:00", "2027-05-28T10:15"),
        seg("BKK", ret_dest, "2027-05-28T12:00", "2027-05-28T19:30"),
    ]
    return mc_offer(out, ret, price_total=price)


def test_only_connected_bookings_count():
    c = get_config()
    v = build_verdict([_rt(7600), _rt(9200)], [], [], c)
    assert v.flight_total == 7600
    assert v.flight.trip_type == "round_trip"


def test_return_reference_matches_roundtrip_airport_and_date():
    c = get_config()
    rt = _rt(7600, origin="FRA")  # return_date = 2027-05-28 (rt_offer default)
    ret_ow_far = offer("return", [seg("USM", "FRA", "2027-05-29T08:00", "2027-05-29T18:00")],
                       price_total=6000, search_date="2027-05-29")
    ret_ow_match = offer("return", [seg("USM", "FRA", "2027-05-28T08:00", "2027-05-28T18:00")],
                         price_total=5200, search_date="2027-05-28")
    v = build_verdict([rt, ret_ow_far, ret_ow_match], [], [], c)
    assert v.return_reference is not None
    assert v.return_reference.price_total == 5200


def test_return_reference_none_for_multicity():
    c = get_config()
    v = build_verdict([_mc(8100)], [], [], c)
    assert v.return_reference is None


def test_multicity_can_win_over_roundtrip():
    c = get_config()
    v = build_verdict([_rt(9500), _mc(8100)], [], [], c)
    assert v.flight_total == 8100
    assert v.flight.trip_type == "multi_city"
    assert any("Multi-City" in n for n in v.notes)


def test_no_connected_booking_means_no_flight_price():
    c = get_config()
    v = build_verdict([], [], [], c)
    assert v.flight is None and v.flight_total is None
    assert any("zusammenhaengende Buchung" in n for n in v.notes)


def test_excluded_offers_are_ignored():
    c = get_config()
    cheap_but_excluded = _rt(5000)
    cheap_but_excluded.excluded = True
    cheap_but_excluded.exclude_reason = "zu_viele_stopps"
    v = build_verdict([cheap_but_excluded, _rt(8000)], [], [], c)
    assert v.flight_total == 8000


def test_verified_group_price_wins_over_cheaper_unconfirmed_estimate():
    # Bugfix 13.09.26 (Nutzer: "sieht aus wie eine 1-Pax-Suche"): eine
    # unbestaetigte 1-Pax-Hochrechnung darf die Kopf-Zusammenfassung nicht
    # gewinnen, wenn fuer dieselbe Route/Termin-Kombination bereits ein
    # ECHT gepruefter (group/split_*) Preis vorliegt - selbst wenn die
    # Hochrechnung selbst (nie verifiziert) guenstiger aussieht.
    c = get_config()
    estimate = _rt(7000)  # pax_mode default "estimated", nie bestaetigt
    verified = _rt(9200)
    verified.pax_mode = "group"
    v = build_verdict([estimate, verified], [], [], c)
    assert v.flight_total == 9200
    assert v.flight.pax_mode == "group"


def test_estimate_still_wins_when_no_verified_counterpart_exists():
    c = get_config()
    estimate = _rt(7000)
    v = build_verdict([estimate], [], [], c)
    assert v.flight_total == 7000


def test_reference_hotel_beats_floor_estimate():
    c = get_config()
    floor = HotelOffer(source="google_hotels", ok=True, price_total=7392,
                       currency="EUR", raw={"basis": "... Floor-Richtwert ..."})
    ref = HotelOffer(source="referenz:CHECK24", ok=True, price_total=8300,
                     currency="EUR", is_reference=True, raw={"note": "3 Zimmer"})
    v = build_verdict([_rt(7000)], [floor, ref], [], c)
    assert v.hotel_total == 8300          # Referenz schlaegt Floor-Richtwert
    assert any("manuelle Referenz" in n for n in v.notes)


def test_concrete_scrape_beats_reference():
    c = get_config()
    concrete = HotelOffer(source="booking", ok=True, price_total=7800,
                          currency="EUR", raw={"basis": "Gesamtpreis 3 Zimmer"})
    ref = HotelOffer(source="referenz:CHECK24", ok=True, price_total=8300,
                     currency="EUR", is_reference=True)
    v = build_verdict([_rt(7000)], [concrete, ref], [], c)
    assert v.hotel_total == 7800


def test_verdict_handles_empty_sources():
    c = get_config()
    v = build_verdict([], [], [], c)
    assert v.flight_total is None and v.winner == "unbekannt"
    assert v.separate_total is None


def test_verdict_compares_separate_vs_package():
    c = get_config()
    hotels = [HotelOffer(source="booking", ok=True, price_total=8000, currency="EUR")]
    packages = [PackageOffer(source="tui", ok=True, price_total=14000, currency="EUR")]
    v = build_verdict([_rt(7000)], hotels, packages, c)
    assert v.separate_total == 15000
    assert v.package_total == 14000
    assert v.winner == "pauschalreise"
    assert v.delta == -1000


def _rt_ret(price, ret_date):
    return rt_offer("FRA", [
        seg("FRA", "DOH", "2027-05-14T16:00", "2027-05-14T23:30"),
        seg("DOH", "USM", "2027-05-15T02:00", "2027-05-15T11:30"),
    ], price_total=price, ret_date=ret_date)


def test_cheaper_raw_flight_loses_when_extra_hotel_night_makes_it_pricier():
    # Bugfix 12.09.26: _pick_flight waehlte vorher rein nach nacktem
    # Flugpreis - ein Rueckflug einen Tag NACH dem konfigurierten
    # hotel_checkout (29.05. statt 28.05.) braucht aber eine Hotelnacht
    # mehr, die der feste Hotelpreis nicht einpreist. Hier hat der 29.05.-
    # Rueckflug den niedrigeren nackten Preis (7900 < 7950), aber durch die
    # zusaetzliche Nacht (~615 EUR) ist die 28.05.-Option in Summe
    # guenstiger - die muss gewinnen, nicht die mit dem kleineren Flugpreis.
    c = get_config()
    hotel = HotelOffer(source="check24", ok=True, price_total=8000.0, currency="EUR",
                       nights=c.trip.nights, per_night=round(8000.0 / c.trip.nights, 2))
    flight_28 = _rt_ret(7950, "2027-05-28")   # kein Extra-Naechte-Bedarf
    flight_29 = _rt_ret(7900, "2027-05-29")   # 1 Nacht mehr noetig, roher Preis aber kleiner
    v = build_verdict([flight_28, flight_29], [hotel], [], c)
    assert v.flight is flight_28
    assert v.flight_total == 7950
    assert v.hotel_total == 8000.0
    assert v.separate_total == 15950.0
    assert any("nicht der guenstigste Einzelflug" in n for n in v.notes)


def test_no_nights_clause_when_cheapest_raw_needs_same_nights_as_chosen():
    # Bugfix (Roadmap Runde 2, Punkt 1.6): delta_word(0) lieferte "0
    # Hotelnaechte weniger" - Unsinn, wenn der guenstigste EINZELFLUG genauso
    # viele Naechte braucht wie die gewaehlte Option (hier gewinnt eine
    # ANDERE Kombination nur, weil sie eine Nacht WENIGER braucht - der
    # guenstigste Einzelflug selbst hat delta=0).
    c = get_config()
    hotel = HotelOffer(source="check24", ok=True, price_total=8000.0, currency="EUR",
                       nights=c.trip.nights, per_night=round(8000.0 / c.trip.nights, 2))
    cheapest_raw = _rt_ret(7900, "2027-05-28")            # delta=0, aber niedrigster Flugpreis
    best = _rt_ret(8100, "2027-05-27")                    # 1 Nacht weniger noetig, teurerer Flug
    v = build_verdict([cheapest_raw, best], [hotel], [], c)
    assert v.flight is best
    matching_notes = [n for n in v.notes if "nicht der guenstigste Einzelflug" in n]
    assert matching_notes
    assert "0 Hotelnacht" not in matching_notes[0]
    assert "braucht" not in matching_notes[0]


def test_hotel_total_scales_up_when_only_flight_returns_a_day_later():
    c = get_config()
    hotel = HotelOffer(source="check24", ok=True, price_total=8000.0, currency="EUR",
                       nights=c.trip.nights, per_night=round(8000.0 / c.trip.nights, 2))
    flight_29 = _rt_ret(7900, "2027-05-29")
    v = build_verdict([flight_29], [hotel], [], c)
    assert v.flight is flight_29
    assert v.flight_total == 7900
    expected_hotel = round(8000.0 + 8000.0 / 13, 2)
    assert v.hotel_total == expected_hotel
    assert v.separate_total == round(7900 + expected_hotel, 2)
    assert any("1 Hotelnacht mehr" in n for n in v.notes)


def test_hotel_nights_follow_actual_departure_date_not_just_return_date():
    # Bugfix 12.09.26 (Nutzerwunsch: Abflug 14. ODER 15., Rueckflug 28. ODER
    # 29.): der Hotel-Checkin haengt vom ECHTEN Abflugdatum ab (+1 Tag
    # Nachtflug), nicht vom fest konfigurierten. Abflug 15./Rueckflug 28. ->
    # Checkin 16., nur 12 statt 13 Naechte -> Hotel muss GUENSTIGER
    # gerechnet werden, nicht gleich bleiben.
    c = get_config()
    hotel = HotelOffer(source="check24", ok=True, price_total=8000.0, currency="EUR",
                       nights=c.trip.nights, per_night=round(8000.0 / c.trip.nights, 2))
    flight_15_28 = rt_offer("FRA", [
        seg("FRA", "DOH", "2027-05-15T16:00", "2027-05-15T23:30"),
        seg("DOH", "USM", "2027-05-16T02:00", "2027-05-16T11:30"),
    ], price_total=7900.0, out_date="2027-05-15", ret_date="2027-05-28")
    v = build_verdict([flight_15_28], [hotel], [], c)
    expected_hotel = round(8000.0 - 8000.0 / 13, 2)  # 1 Nacht weniger als Baseline
    assert v.hotel_total == expected_hotel
    assert v.separate_total == round(7900.0 + expected_hotel, 2)


def test_note_mentions_search_date_not_just_return_date():
    # Bugfix 13.09.26: die Notiz verglich vorher nur return_date mit
    # hotel_checkout - bei zwei Angeboten mit IDENTISCHEM Rueckflugdatum
    # aber unterschiedlichem ABFLUGdatum (14. vs. 15.05., beide -> 28.05.)
    # klang das wie ein Widerspruch ("Rueckflug am 28.05. ist 1 Nacht
    # frueher als der Checkout (28.05.)" - Termine identisch, aber die
    # Aussage bezog sich in Wahrheit auf die abweichende Abflug-/Checkin-
    # Seite). Die Notiz muss jetzt beide Daten (search_date -> return_date)
    # nennen, damit klar ist, WAS sich unterscheidet.
    c = get_config()
    hotel = HotelOffer(source="check24", ok=True, price_total=8000.0, currency="EUR",
                       nights=c.trip.nights, per_night=round(8000.0 / c.trip.nights, 2))
    flight_14_28 = _rt_ret(7950, "2027-05-28")  # Baseline: 0 Naechte-Differenz
    flight_15_28 = rt_offer("FRA", [
        seg("FRA", "DOH", "2027-05-15T16:00", "2027-05-15T23:30"),
        seg("DOH", "USM", "2027-05-16T02:00", "2027-05-16T11:30"),
    ], price_total=7900.0, out_date="2027-05-15", ret_date="2027-05-28")
    v = build_verdict([flight_14_28, flight_15_28], [hotel], [], c)
    assert v.flight is flight_15_28  # guenstiger nackter Preis UND weniger Naechte -> gewinnt klar
    assert any("2027-05-15" in n and "2027-05-28" in n for n in v.notes)
