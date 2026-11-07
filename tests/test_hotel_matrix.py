from datetime import date

from app.config import get_config
from app.sources.hotels import _cfg_for_dates, hotel_date_combos


def test_hotel_combos_follow_flight_date_pairs_plus_one_day():
    c = get_config().model_copy(deep=True)
    c.trip.booked_flight = None
    combos = hotel_date_combos(c)
    assert (date(2027, 5, 15), date(2027, 5, 28)) in combos
    assert (date(2027, 5, 16), date(2027, 5, 29)) in combos
    assert len(combos) == 4


def test_cfg_for_dates_overrides_only_hotel_dates():
    c = get_config()
    v = _cfg_for_dates(c, date(2027, 5, 16), date(2027, 5, 29))
    assert v.trip.hotel_checkin == date(2027, 5, 16)
    assert v.trip.hotel_checkout == date(2027, 5, 29)
    assert v.trip.nights == 13
    assert c.trip.hotel_checkin == date(2027, 5, 15)   # Original unveraendert


def test_booked_flight_limits_hotel_search_to_one_period():
    # Nutzer 07.11.26: Flug 14.->28. gebucht -> nur Hotel 15.->28.
    from app.config import BookedFlight

    c = get_config().model_copy(deep=True)
    c.trip.booked_flight = BookedFlight(price_total=7183.36, outbound_date=date(2027, 5, 14),
                                        return_date=date(2027, 5, 28))
    assert hotel_date_combos(c) == [(date(2027, 5, 15), date(2027, 5, 28))]
