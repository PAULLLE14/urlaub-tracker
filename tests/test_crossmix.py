from datetime import date

from app.config import get_config
from app.offers import HotelOffer
from app.sources.hotels.crossmix import cross_mix

CI, CO = date(2027, 5, 15), date(2027, 5, 28)
RAW = {"checkin": "2027-05-15", "checkout": "2027-05-28"}


def _offer(source, total, per_size, **extra):
    return HotelOffer(source=source, ok=True, price_total=total, currency="EUR", nights=13,
                      raw={**RAW, "per_room_size": per_size, **extra})


def test_cross_mix_takes_cheapest_room_size_across_sources():
    cfg = get_config()
    # Lauf #302 vom 21.11.26: 3er am guenstigsten bei CHECK24, 2er bei Etrip (Google)
    check24 = _offer("check24", 8775.0,
                     {"3": {"cheapest": 3046.0, "url": "c3", "supplier": "Stayforlong"},
                      "2": {"cheapest": 2683.0, "url": "c2", "supplier": "Agoda"}})
    google = _offer("Etrip.net (Google)", 10188.0,
                    {"3": {"cheapest": 3800.0, "url": "g3", "provider": "Etrip.net"},
                     "2": {"cheapest": 2547.0, "url": "g2", "provider": "Etrip.net"}})
    mix = cross_mix(cfg, [check24, google], CI, CO)
    assert mix is not None
    assert mix.price_total == 2 * 3046.0 + 2547.0 == 8639.0
    assert mix.source == "Mix: CHECK24 (Stayforlong) (2×3er) + Etrip.net (1×2er)"
    assert mix.raw["per_room_size"]["3"]["url"] == "c3" and mix.raw["per_room_size"]["2"]["url"] == "g2"


def test_cross_mix_none_when_a_single_source_is_already_cheapest():
    cfg = get_config()
    check24 = _offer("check24", 8775.0,
                     {"3": {"cheapest": 3046.0, "url": "c3", "supplier": "S"},
                      "2": {"cheapest": 2683.0, "url": "c2", "supplier": "A"}})
    google = _offer("X (Google)", 9500.0,
                    {"3": {"cheapest": 3400.0, "url": "g3", "provider": "X"},
                     "2": {"cheapest": 2800.0, "url": "g2", "provider": "X"}})
    assert cross_mix(cfg, [check24, google], CI, CO) is None


def test_cross_mix_applies_fee_factor_to_direct_santiburi_prices():
    cfg = get_config()
    direct = _offer("santiburi_official", 7000.0,
                    {"3": {"cheapest": 2500.0, "url": "d3"}, "2": {"cheapest": 2000.0, "url": "d2"}},
                    price_total_incl_fees_estimate=9400.0)
    other = _offer("Y (Google)", 9600.0,
                   {"3": {"cheapest": 3200.0, "url": "y3", "provider": "Y"},
                    "2": {"cheapest": 2300.0, "url": "y2", "provider": "Y"}})
    # Direkt 3er: 2500*1.187 = 2967,5 < 3200; 2er: 2000*1.187 = 2374 > 2300 -> Mix Direkt (3er) + Y (2er)
    mix = cross_mix(cfg, [direct, other], CI, CO)
    assert mix is not None and "Santiburi direkt (2×3er)" in mix.source and "Y (1×2er)" in mix.source
