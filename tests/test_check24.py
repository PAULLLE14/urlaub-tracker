from app.sources.hotels.check24 import _occupancy_param, _parse_rows, _url

# Zimmeraufteilungs-Tests (room_split/candidate_allocations) leben jetzt in
# tests/test_room_split.py - check24.py hat keine eigene _room_split-Funktion
# mehr, sondern nutzt app/sources/room_split.py direkt (13.09.26,
# Nutzervorgabe: alle sinnvollen Aufteilungen vergleichen statt einer festen).


def test_occupancy_param_encoding():
    assert _occupancy_param(2) == "[A|A]"
    assert _occupancy_param(3) == "[A|A|A]"


def test_url_builds_check24_deep_link():
    from datetime import date

    url = _url("Santiburi Koh Samui-9203019", date(2027, 5, 15), date(2027, 5, 28), 3)
    assert url == (
        "https://hotel.check24.de/search/Santiburi%20Koh%20Samui-9203019/"
        "2027-05-15/2027-05-28/%5BA%7CA%7CA%5D/hotel.html"
    )


def test_parse_rows_extracts_price_refundable_supplier():
    text = (
        "Suite (Duplex)\n"
        "2.375 €\n"
        "23,74 € als Smily\n"
        "Punkte sammeln\n"
        "Sehr gutes Frühstück im Preis inbegriffen\n"
        "Nicht kostenlos stornierbar\n"
        "Später zahlen\n"
        "Details zum Angebot von Expedia\n"
        "Zimmer: Suite (Duplex)\n"
        "0\n"
        "2.858 €\n"
        "28,58 € als Smily\n"
        "Punkte sammeln\n"
        "Kostenlos stornierbar bis 23:59 Uhr am 14. Sep. 2026\n"
        "Anzahlung beim Buchen\n"
        "Details zum Angebot von alltours\n"
        "Zimmer: Suite\n"
        "0\n"
    )
    rows = _parse_rows(text)
    assert len(rows) == 2
    assert rows[0] == {"price": 2375.0, "refundable": False, "supplier": "Expedia",
                        "room": "Suite (Duplex)"}
    assert rows[1] == {"price": 2858.0, "refundable": True, "supplier": "alltours",
                        "room": "Suite"}


def test_parse_rows_ignores_unrelated_text():
    assert _parse_rows("kein Preis hier, nur Text") == []


def _row_block(price: str, supplier: str, room: str = "") -> str:
    zimmer = f"Zimmer: {room}\n" if room else ""
    return (
        f"{price} €\n5,50 € als Smily\nPunkte sammeln\n"
        f"Sehr gutes Frühstück im Preis inbegriffen\nNicht kostenlos stornierbar\n"
        f"Später zahlen\nDetails zum Angebot von {supplier}\n{zimmer}"
    )


def test_parse_rows_drops_implausible_low_outlier():
    # Live-Fund 13.09.26: Tapstay zeigte fuer "1 Zimmer/2 Erwachsene" 551EUR
    # fuer dieselbe Zimmerkategorie ("Deluxe Villa, Beachfront"), die andere
    # Anbieter fuer 8.500-10.600EUR zeigten - ein irrefuehrender Ausreisser,
    # kein echter Preis. Das globale Minimum blind zu nehmen haette den
    # Hotelpreis um ein Vielfaches zu niedrig ausgewiesen.
    text = (
        _row_block("551", "Tapstay")
        + _row_block("2267", "Agoda")
        + _row_block("2383", "Expedia")
        + _row_block("2415", "Booking.com")
        + _row_block("8514", "stayforlong")
    )
    rows = _parse_rows(text)
    prices = sorted(r["price"] for r in rows)
    assert 551.0 not in prices
    assert prices[0] == 2267.0


def test_parse_rows_keeps_genuinely_close_prices():
    # Gegenprobe: normale Preisstreuung zwischen Anbietern (kein Ausreisser)
    # darf NICHT herausgefiltert werden.
    text = (
        _row_block("2200", "Agoda")
        + _row_block("2350", "Expedia")
        + _row_block("2500", "Booking.com")
    )
    rows = _parse_rows(text)
    assert sorted(r["price"] for r in rows) == [2200.0, 2350.0, 2500.0]


def test_parse_rows_filters_to_hinted_room_category():
    # Live-Fund 13.09.26: CHECK24 listet auf einer Suchseite ALLE
    # Zimmerkategorien (Standard bis Villa). Ohne Kategorie-Filter gewinnt
    # das billige Standardzimmer statt der konfigurierten Villa - das
    # erklaerte die 1.653EUR statt ~7-8.000EUR Diskrepanz, nicht ein
    # einzelner Preis-Ausreisser.
    text = (
        _row_block("551", "Tapstay", "Standard Doppelzimmer")
        + _row_block("620", "Agoda", "Standard Doppelzimmer")
        + _row_block("8514", "Expedia", "Grand Reserve Villa, Beachfront")
        + _row_block("8900", "Booking.com", "Grand Reserve Villa")
    )
    rows = _parse_rows(text, room_hint="Grand Reserve Villa")
    prices = sorted(r["price"] for r in rows)
    assert prices == [8514.0, 8900.0]


def test_parse_rows_falls_back_when_no_room_matches_hint():
    text = (
        _row_block("2200", "Agoda", "Suite")
        + _row_block("2350", "Expedia", "Suite Deluxe")
    )
    rows = _parse_rows(text, room_hint="Grand Reserve Villa")
    assert sorted(r["price"] for r in rows) == [2200.0, 2350.0]
