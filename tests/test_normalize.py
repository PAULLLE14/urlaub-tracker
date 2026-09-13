from app.logic.normalize import dedup_flights
from tests.helpers import rt_offer, seg


def test_dedup_keeps_group_confirmed_row_separate_from_estimated():
    # Bugfix 11.09.26: eine 1-Pax-Hochrechnung ("estimated") und eine echte
    # Gruppensuche ("group") fuer GENAU denselben Flug (gleiche Route/Zeit/
    # Airline) sind nicht dasselbe Angebot - dedup_key enthielt pax_mode
    # vorher NICHT, dedup_flights() haette die beiden verschmolzen und die
    # TEURERE (aber echte, bestaetigte) Gruppen-Zeile stillschweigend
    # verworfen, weil nur nach Preis entschieden wurde.
    est = rt_offer("FRA", [seg("FRA", "USM", "2027-05-14T16:00", "2027-05-15T11:30",
                               airline="Qatar Airways")], price_total=7264.0)
    est.pax_mode = "estimated"
    est.excluded = True
    est.exclude_reason = "gruppen_check: fuer 8 Personen nicht in diesem Preis verfuegbar"

    grp = rt_offer("FRA", [seg("FRA", "USM", "2027-05-14T16:00", "2027-05-15T11:30",
                               airline="Qatar Airways")], price_total=9200.0)
    grp.pax_mode = "group"

    result = dedup_flights([est, grp])
    assert len(result) == 2, "estimated und group duerfen nicht verschmolzen werden"
    modes = {o.pax_mode for o in result}
    assert modes == {"estimated", "group"}
    kept_group = next(o for o in result if o.pax_mode == "group")
    assert not kept_group.excluded
    assert kept_group.price_total == 9200.0


def test_dedup_never_lets_excluded_offer_win_over_valid_at_same_key():
    # Sicherheitsnetz fuer den Fall, dass zwei Angebote trotzdem denselben
    # vollen dedup_key teilen (gleicher pax_mode inklusive): das guenstigere
    # darf nicht gewinnen, wenn es ausgeschlossen ist und das teurere nicht -
    # sonst wuerde eine ungueltige (aber billiger aussehende) Zeile eine
    # tatsaechlich buchbare verdecken.
    cheap_but_excluded = rt_offer("FRA", [seg("FRA", "USM", "2027-05-14T16:00",
                                              "2027-05-15T11:30", airline="Condor")],
                                  price_total=5000.0)
    cheap_but_excluded.excluded = True
    cheap_but_excluded.exclude_reason = "airline_ausgeschlossen (Condor)"

    valid_pricier = rt_offer("FRA", [seg("FRA", "USM", "2027-05-14T16:00",
                                         "2027-05-15T11:30", airline="Condor")],
                             price_total=6000.0)

    result = dedup_flights([cheap_but_excluded, valid_pricier])
    assert len(result) == 1
    assert not result[0].excluded
    assert result[0].price_total == 6000.0
