from app.sources.flights_kayak import _parse_cards, _url

SELF_TRANSFER_CARD = """Zu den Ergebnisdetails
Eigenständiger Transfer
17:15 – 20:30+1
FRAFrankfurt am Main
-
USMKo Samui
2 Stopps
DOH
 2:15 Std. Aufenthalt, Doha Hamad Intl
,
BKK
 6:00 Std. Zwischenstopp, Eigenständiger Transfer am Bangkok-Suvarnabhumi (BKK)
22:15 Std.
11:45 – 7:15+1
USMKo Samui
-
FRAFrankfurt am Main
2 Stopps
BKK
 7:00 Std. Zwischenstopp, Eigenständiger Transfer am Bangkok-Suvarnabhumi (BKK)
, DOH
 3:20 Std. Aufenthalt, Doha Hamad Intl
24:30 Std.
Mehrere Fluglinien
1
1
852 €
Basic + Economy Class + Economy Classic
Auswählen
"""

PROTECTED_CARD = """Zu den Ergebnisdetails
17:15 – 16:30+1
FRAFrankfurt am Main
-
USMKo Samui
2 Stopps
DOH
 2:15 Std. Aufenthalt, Doha Hamad Intl
, BKK
 1:35 Std. Aufenthalt, Bangkok-Suvarnabhumi
18:15 Std.
21:00 – 14:35+1
USMKo Samui
-
FRAFrankfurt am Main
2 Stopps
BKK
 4:00 Std. Aufenthalt, Bangkok-Suvarnabhumi
, DOH
 4:10 Std. Aufenthalt, Doha Hamad Intl
22:35 Std.
Qatar Airways, Bangkok Airways
1
1
884 €
Economy Classic + Economy Class
Auswählen
"""


def test_url_format():
    from datetime import date
    url = _url("FRA", "USM", date(2027, 5, 14), date(2027, 5, 28), 1)
    assert url == "https://www.kayak.de/flights/FRA-USM/2027-05-14/2027-05-28/1adults?sort=price_a"


def test_self_transfer_card_is_excluded():
    # single_ticket_only-Aequivalent: getrennte Tickets (kein gemeinsamer
    # Umbuchungsschutz) duerfen nie als Angebot durchrutschen.
    assert _parse_cards(SELF_TRANSFER_CARD) == []


def test_protected_card_is_parsed_with_real_data():
    cards = _parse_cards(PROTECTED_CARD)
    assert len(cards) == 1
    c = cards[0]
    assert c["price"] == 884.0
    assert c["airlines"] == ["Qatar Airways", "Bangkok Airways"]
    assert c["out_origin"] == "FRA" and c["out_dest"] == "USM"
    assert c["out_layovers"] == ["DOH", "BKK"]
    assert c["out_duration_min"] == 18 * 60 + 15
    assert c["ret_origin"] == "USM" and c["ret_dest"] == "FRA"
    assert c["ret_layovers"] == ["BKK", "DOH"]
    assert c["ret_duration_min"] == 22 * 60 + 35


def test_mixed_results_only_keeps_protected():
    cards = _parse_cards(SELF_TRANSFER_CARD + PROTECTED_CARD)
    assert len(cards) == 1
    assert cards[0]["price"] == 884.0
