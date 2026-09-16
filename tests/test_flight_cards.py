from datetime import date

from app.sources.flight_cards import (
    build_approx_segments,
    confirmed_dates,
    implausible_price_reason,
    parse_booking_options,
    parse_cards,
)

SAMPLE_BOOKING_OPTIONS = """Zusammenfassung des Flugreiseplans
Teilen
Frankfurt am Main
Ko Samui
Hin- und RückreiseEconomy Class
8 Passagiere
7.080 €
Niedrigster Gesamtpreis
Ausgewählte Flüge
Buchungsoptionen
Sortierung der Optionen
Weitere Informationen zu Buchungsoptionen
Bei Qatar Airways buchenFluggesellschaft
7.352 €
Weiter
Ansichtsoptionen
Bei lastminute.com buchen
7.080 €
Weiter
Ansichtsoptionen
Bei Tripado buchen
7.389 €
Weiter
Ansichtsoptionen
"""

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

SAMPLE_RT = """15:15
 –
17:45+1
Qatar Airways
21 Std. 30 Min.
FRA–USM
1 Stopp
DOH
668 kg CO2e
Durchschn. (geschätzt)
7.939 €
Hin und zurück
16:55
 –
16:35+1
Condor, Bangkok Airways
18 Std. 40 Min.
FRA–USM
1 Stopp
BKK
617 kg CO2e
-10 % (geschätzt)
9.200 €
Hin und zurück"""


def test_parse_cards_extracts_price_stops_layovers():
    cards = parse_cards(SAMPLE_OUTBOUND, "gesamte Reise")
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
    cards = parse_cards(SAMPLE_NONSTOP, "gesamte Reise")
    assert len(cards) == 1
    assert cards[0]["stops"] == 0
    assert cards[0]["layovers"] == []
    assert cards[0]["price"] == 199.0


def test_parse_cards_skips_mismatched_layover_count():
    # Absichtlich verstuemmelt: "2 Stopps" aber nur 1 Layover-Code im Text -
    # darf NICHT als (falsche) 2-Stopp-Karte durchrutschen.
    bad = SAMPLE_OUTBOUND.replace("VIE, BKK\n668 kg", "VIE\n668 kg")
    cards = parse_cards(bad, "gesamte Reise")
    assert len(cards) == 1  # nur die zweite (unveraenderte) Karte bleibt gueltig


def test_parse_cards_different_end_marker_for_round_trip():
    # Bugfix 14.09.26 (externe Review, Punkt A1): der Gruppen-Check braucht
    # die VOLLEN Kartendaten (Airline/Zeiten), nicht nur den Preis - beide
    # Karten hier haben unterschiedliche Airlines/Zeiten bei unterschiedlichem
    # Preis, die guenstigere (Qatar, 7.939) darf nicht mit den Daten der
    # anderen Karte (Condor, 9.200) vermischt werden.
    cards = parse_cards(SAMPLE_RT, "Hin und zurück")
    assert len(cards) == 2
    cheapest = min(cards, key=lambda c: c["price"])
    assert cheapest["price"] == 7939.0
    assert cheapest["airlines"] == ["Qatar Airways"]
    assert cheapest["layovers"] == ["DOH"]


def test_implausible_price_reason_flags_outside_window():
    # Roadmap Runde 2, Punkt 1.1: CheckRun #17 zeigte 7.391 EUR p.P. auf einer
    # Multi-City-Karte - ausserhalb des plausiblen Fensters, vermutlich ein
    # Parse-Fehler statt ein echtes Angebot.
    assert implausible_price_reason(7391.0) != ""
    assert "parse_suspect" in implausible_price_reason(7391.0)
    assert implausible_price_reason(50.0) != ""


def test_implausible_price_reason_accepts_normal_range():
    assert implausible_price_reason(992.375) == ""
    assert implausible_price_reason(400.0) == ""
    assert implausible_price_reason(3000.0) == ""


def test_confirmed_dates_parses_googles_own_text():
    # Live verifiziert 15.09.26 auf einer echten FRA-USM-Round-Trip-Suche
    # (Roadmap Runde 2/3, Punkt 2.3/6.4).
    text = ("Für Flüge von Frankfurt am Main nach Ko Samui mit Abflug am "
           "2027-05-14 und Ankunft am 2027-05-28 Preise beobachten")
    assert confirmed_dates(text) == ("2027-05-14", "2027-05-28")


def test_confirmed_dates_none_when_block_missing():
    assert confirmed_dates("irgendein anderer Seitentext ohne den Baustein") is None


def test_parse_booking_options_finds_cheaper_third_party():
    # Live-Fund 16.09.26: die Listenkarte zeigt nur den Airline-Preis
    # (7.352 EUR Qatar), die Buchungsoptionen-Seite zeigt lastminute.com
    # fast 300 EUR guenstiger (7.080 EUR) - das ist der eigentliche
    # "Niedrigster Gesamtpreis", den Google selbst oben ausweist.
    result = parse_booking_options(SAMPLE_BOOKING_OPTIONS)
    assert result["lowest_total"] == 7080.0
    assert len(result["options"]) == 3
    assert result["options"][0]["provider"] == "lastminute.com"
    assert result["options"][0]["price"] == 7080.0
    assert result["options"][0]["is_airline"] is False
    qatar = next(o for o in result["options"] if o["provider"] == "Qatar Airways")
    assert qatar["price"] == 7352.0
    assert qatar["is_airline"] is True


def test_build_segments_real_airports_synthetic_times():
    segs = build_approx_segments("STR", "USM", ["VIE", "BKK"], "15:15",
                                 21 * 60 + 30, date(2027, 5, 14))
    assert len(segs) == 3
    codes = [segs[0].from_airport] + [s.to_airport for s in segs]
    assert codes == ["STR", "VIE", "BKK", "USM"]
    assert segs[0].departure.hour == 15 and segs[0].departure.minute == 15
    # Gesamtdauer der synthetischen Segmente muss (bis auf Rundung) der
    # echten Gesamtreisezeit entsprechen.
    total = int((segs[-1].arrival - segs[0].departure).total_seconds() // 60)
    assert abs(total - (21 * 60 + 30)) <= 3
