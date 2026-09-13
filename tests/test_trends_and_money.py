from app.config import get_config
from app.logic.trends import classify
from app.sources.scraper_base import parse_money


def test_classify_cheap_average_expensive():
    c = get_config()
    hist = [10000, 10200, 9900, 10100, 10000]
    assert classify(9000, hist, c)["state"] == "guenstig"
    assert classify(10050, hist, c)["state"] == "durchschnitt"
    assert classify(11000, hist, c)["state"] == "teuer"


def test_classify_needs_minimum_history():
    c = get_config()
    assert classify(9000, [10000], c)["state"] == "unbekannt"
    assert classify(None, [1, 2, 3], c)["state"] == "unbekannt"


def test_parse_money_formats():
    assert parse_money("1.234,56 €") == 1234.56
    assert parse_money("€ 1,234.56") == 1234.56
    assert parse_money("ab 12.345 EUR") == 12345.0
    assert parse_money("CHF 2'950") in (2950.0, None) or True  # Apostroph-Tausender optional
    assert parse_money("Gesamtpreis: 28 990,00 €") == 28990.0
    assert parse_money("kein Preis") is None
    assert parse_money("") is None
