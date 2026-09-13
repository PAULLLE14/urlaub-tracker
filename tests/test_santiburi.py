from app.sources.hotels.santiburi import _parse_rows, _url


def test_url_builds_synxis_deep_link():
    from datetime import date

    url = _url("34270", "95417", date(2027, 5, 15), date(2027, 5, 28), 3)
    assert "chain=34270" in url
    assert "hotel=95417" in url
    assert "adult=3" in url
    assert "arrive=2027-05-15" in url


def _block(per_night: str, total: str, nights: str, room: str) -> str:
    return (
        f"€{per_night}\nPer Night\n€{total} Total for {nights} nights\n"
        f"Free cancellation\n{room}\n"
    )


def test_parse_rows_filters_to_hinted_room_category():
    # Gleiche Bugklasse wie hotels/check24.py (13.09.26): die Buchungsseite
    # listet ALLE Zimmerkategorien - ohne Filter gewinnt das billige
    # Standardzimmer statt der konfigurierten Villa.
    text = (
        _block("300", "3900", "13", "STANDARD DOUBLE ROOM")
        + _block("650", "8450", "13", "GRAND RESERVE VILLA BEACHFRONT")
    )
    rows = _parse_rows(text, room_hint="Grand Reserve Villa")
    assert len(rows) == 1
    assert rows[0]["total"] == 8450.0


def test_parse_rows_falls_back_when_no_room_matches_hint():
    text = _block("300", "3900", "13", "STANDARD DOUBLE ROOM")
    rows = _parse_rows(text, room_hint="Grand Reserve Villa")
    assert len(rows) == 1
