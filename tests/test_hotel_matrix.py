from datetime import date

from app.config import get_config
from app.sources.hotels import _cfg_for_dates, hotel_date_combos


def test_hotel_combos_follow_flight_date_pairs_plus_one_day():
    c = get_config()
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
