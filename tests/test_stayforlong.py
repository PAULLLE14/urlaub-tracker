from datetime import date

from app.sources.hotels.stayforlong import _url, parse_rates

# Originaltext der Stayforlong-Seite (Nutzer-Screenshot/Live-Abruf 26.09.26).
SAMPLE = (
    "(x4) Duplex suite, doppelbett oder zwei einzelbetten\n2 Erwachsene\nZimmerdetails anzeigen\n"
    "Frühstück inklusive\nBester Preis\nJetzt bezahlen\nNicht erstattbar\n-33% Heute\n12.049 €\n8.086 €\n"
    "622 €\n/ Nacht\n(13 Nächte)\nMwSt. mit inbegriffen\n\nBuchen\n\n"
    "Frühstück inklusive\nKOSTENLOSE Stornierung vor dem 03. Mai 2027\nBezahlen Sie am 03. Mai 2027\n"
    "-27% Heute\n15.481 €\n11.388 €\n876 €\n/ Nacht\n(13 Nächte)\nMwSt. mit inbegriffen\n\nBuchen\n"
)


def test_parse_rates_reads_total_for_all_rooms_and_flags():
    rows = parse_rates(SAMPLE)
    assert [r["total"] for r in rows] == [8086.0, 11388.0]
    assert rows[0]["non_refundable"] and rows[0]["breakfast"] and not rows[0]["refundable"]
    assert rows[1]["refundable"] and not rows[1]["non_refundable"]
    assert all(r["nights"] == 13 for r in rows)


def test_parse_rates_rejects_inconsistent_numbers():
    bad = "8.086 €\n999 €\n/ Nacht\n(13 Nächte)"
    assert parse_rates(bad) == []


def test_url_encodes_room_shape():
    url = _url(date(2027, 5, 15), date(2027, 5, 28), [2, 2, 2, 2])
    assert "adults=2,2,2,2" in url and "checkIn=2027-05-15" in url and "children=%21%21%21" in url
