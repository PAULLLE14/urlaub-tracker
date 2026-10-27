from types import SimpleNamespace

from app.config import get_config
from app.logic.trends import comparable_hotel_price


def _row(source, price, raw=None, ok=True, ref=False):
    return SimpleNamespace(source=source, price_total=price, raw=raw or {}, ok=ok, is_reference=ref)


def test_history_uses_price_including_fees_and_ignores_references_and_floors():
    c = get_config()
    rows = [
        _row("santiburi_official", 7348.0),                    # ohne Gebuehren -> x 1,187
        _row("check24", 9296.0),
        _row("referenz:CHECK24", 8300.0, ref=True),           # manuell -> ignoriert
        _row("google_hotels", 5000.0, {"estimate": True}),    # Floor -> ignoriert
    ]
    assert comparable_hotel_price(rows, c) == round(7348.0 * (1.10 * 1.07 + 0.01), 2)


def test_history_ignores_other_date_ranges():
    c = get_config()
    rows = [_row("check24", 8444.0, {"checkin": "2027-05-16", "checkout": "2027-05-28"}),
            _row("check24", 9296.0, {"checkin": "2027-05-15", "checkout": "2027-05-28"})]
    assert comparable_hotel_price(rows, c) == 9296.0


def test_history_none_when_no_usable_rows():
    assert comparable_hotel_price([_row("x", 1.0, ok=False)], get_config()) is None


def test_snapshot_won_by_stale_reference_is_recomputed_from_real_scrapes():
    import app.logic.trends as tr

    rows = [_row("referenz:Expedia", 7722.0, ref=True), _row("check24", 9916.0)]
    session = SimpleNamespace(scalars=lambda q: iter(rows))
    snap = SimpleNamespace(run_id=1, hotel_total=7722.0, verdict={"hotel": {"is_reference": True}})
    assert tr._hotel_value(session, snap, get_config()) == 9916.0


def test_snapshot_with_only_reference_and_no_scrape_is_dropped():
    import app.logic.trends as tr

    rows = [_row("referenz:Expedia", 7722.0, ref=True)]
    session = SimpleNamespace(scalars=lambda q: iter(rows))
    snap = SimpleNamespace(run_id=1, hotel_total=7722.0, verdict={"hotel": {"is_reference": True}})
    assert tr._hotel_value(session, snap, get_config()) is None
