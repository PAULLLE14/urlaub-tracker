from datetime import date

from app.sources.flights_multicity_browser import _build_segments, _parse_cards

SAMPLE_OUTBOUND = """Suchergebnisse
4 Ergebnisse.
Alle Flüge nach Ko Samui
Nach beliebtesten Flügen sortiert
15:15
 –
17:45+1
Austrian, Bangkok Airways
21 Std. 30 Min.
STR–USM
2 Stopps
VIE, BKK
668 kg CO2e
Durchschn. (geschätzt)
1.369 €
gesamte Reise
16:55
 –
16:35+1
Lufthansa, Bangkok Airways
18 Std. 40 Min.
STR–USM
2 Stopps
MUC, BKK
617 kg CO2e
-10 % (geschätzt)
1.594 €
gesamte Reise
Mehr Flüge ansehen"""

SAMPLE_NONSTOP = """15:00
 –
16:00
Bangkok Airways
1 Std.
BKK–USM
Nonstop
199 kg CO2e
Durchschn. (geschätzt)
199 €
gesamte Reise"""


def test_parse_cards_extracts_price_stops_layovers():
    cards = _parse_cards(SAMPLE_OUTBOUND)
    assert len(cards) == 2
    a, b = cards
    assert a["price"] == 1369.0
    assert a["stops"] == 2
    assert a["layovers"] == ["VIE", "BKK"]
    assert a["origin"] == "STR" and a["dest"] == "USM"
    assert a["airlines"] == ["Austrian", "Bangkok Airways"]
    assert a["duration_min"] == 21 * 60 + 30
    assert b["layovers"] == ["MUC", "BKK"]


def test_parse_cards_handles_nonstop():
    cards = _parse_cards(SAMPLE_NONSTOP)
    assert len(cards) == 1
    assert cards[0]["stops"] == 0
    assert cards[0]["layovers"] == []
    assert cards[0]["price"] == 199.0


def test_parse_cards_skips_mismatched_layover_count():
    # Absichtlich verstuemmelt: "2 Stopps" aber nur 1 Layover-Code im Text -
    # darf NICHT als (falsche) 2-Stopp-Karte durchrutschen.
    bad = SAMPLE_OUTBOUND.replace("VIE, BKK\n668 kg", "VIE\n668 kg")
    cards = _parse_cards(bad)
    assert len(cards) == 1  # nur die zweite (unveraenderte) Karte bleibt gueltig


def test_build_segments_real_airports_synthetic_times():
    segs = _build_segments("STR", "USM", ["VIE", "BKK"], "15:15", 21 * 60 + 30, date(2027, 5, 14))
    assert len(segs) == 3
    codes = [segs[0].from_airport] + [s.to_airport for s in segs]
    assert codes == ["STR", "VIE", "BKK", "USM"]
    assert segs[0].departure.hour == 15 and segs[0].departure.minute == 15
    # Gesamtdauer der synthetischen Segmente muss (bis auf Rundung) der
    # echten Gesamtreisezeit entsprechen.
    total = int((segs[-1].arrival - segs[0].departure).total_seconds() // 60)
    assert abs(total - (21 * 60 + 30)) <= 3
