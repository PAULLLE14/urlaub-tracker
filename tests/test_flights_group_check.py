from datetime import date

from app.sources.flights import _flag_duplicate_estimates
from tests.helpers import rt_offer, seg


def _fra_offer(price=7264.0):
    return rt_offer("FRA", [
        seg("FRA", "DOH", "2027-05-14T17:15", "2027-05-15T00:15", airline="Qatar Airways"),
        seg("DOH", "USM", "2027-05-15T02:00", "2027-05-15T12:55", airline="Qatar Airways"),
    ], price_total=price)


def test_flags_all_price_matching_duplicates_not_just_first():
    # Bugfix 12.09.26: Google lieferte denselben guenstigsten Flug (FRA-USM
    # Qatar Airways) live reproduzierbar 3x identisch in einer Antwort - der
    # Gruppen-Check darf nicht nur die eine Objekt-Referenz flaggen, die
    # best_by_key zufaellig zuerst sah, sonst ueberlebt ein unmarkiertes
    # Duplikat den dedup-Schritt und die widerlegte Buchung erscheint
    # trotzdem wieder als "guenstigste" (siehe dedup_flights()-Fix, das ein
    # excluded-Angebot nie mehr ueber ein gueltiges gewinnen laesst).
    twin_a = _fra_offer()
    twin_b = _fra_offer()
    twin_c = _fra_offer()
    other_price = _fra_offer(price=8088.0)  # andere Route/Preis - darf NICHT geflaggt werden
    offers = [twin_a, twin_b, twin_c, other_price]
    key = (twin_a.trip_type, twin_a.origin, twin_a.destination, twin_a.search_date, twin_a.return_date)

    flagged = _flag_duplicate_estimates(offers, key, twin_a.price_total,
                                        "gruppen_check: nicht verfuegbar")

    assert flagged == 3
    assert twin_a.group_check_unconfirmed and twin_b.group_check_unconfirmed and twin_c.group_check_unconfirmed
    assert not other_price.group_check_unconfirmed


def test_does_not_flag_group_mode_or_different_key():
    twin = _fra_offer()
    group_mode_same_price = _fra_offer()
    group_mode_same_price.pax_mode = "group"
    different_date = rt_offer("FRA", [
        seg("FRA", "DOH", "2027-05-25T17:15", "2027-05-26T00:15"),
        seg("DOH", "USM", "2027-05-26T02:00", "2027-05-26T12:55"),
    ], price_total=7264.0, out_date="2027-05-25", ret_date="2027-05-29")
    offers = [twin, group_mode_same_price, different_date]
    key = (twin.trip_type, twin.origin, twin.destination, twin.search_date, twin.return_date)

    flagged = _flag_duplicate_estimates(offers, key, twin.price_total, "x")

    assert flagged == 1
    assert twin.group_check_unconfirmed == "x"
    assert not group_mode_same_price.group_check_unconfirmed
    assert not different_date.group_check_unconfirmed
