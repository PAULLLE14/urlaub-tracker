from app.collector import light_trigger_reason


def test_trigger_below_target():
    assert "Zielpreis" in light_trigger_reason(8700.0, 9170.0, 8816.0, 5.0)


def test_trigger_on_drop_vs_last_full_run():
    assert "unter dem letzten vollen Lauf" in light_trigger_reason(8600.0, 9170.0, None, 5.0)


def test_no_trigger_for_small_moves_or_missing_prices():
    assert light_trigger_reason(9000.0, 9170.0, 8816.0, 5.0) == ""
    assert light_trigger_reason(None, 9170.0, 8816.0, 5.0) == ""
    assert light_trigger_reason(9100.0, None, None, 5.0) == ""
