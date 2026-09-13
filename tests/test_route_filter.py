from app.config import get_config
from app.logic.route_filter import apply_constraints
from tests.helpers import mc_offer, offer, seg


def cfg():
    return get_config()


def test_outbound_before_time_window_excluded():
    # Hinflug hebt 12:30 ab -> vor dem STR-Cutoff (16:30) -> raus
    o = offer("outbound", [
        seg("STR", "DOH", "2027-05-14T12:30", "2027-05-14T20:00"),
        seg("DOH", "USM", "2027-05-14T23:00", "2027-05-15T08:30"),
    ])
    apply_constraints([o], cfg())
    assert o.excluded and o.exclude_reason.startswith("abflug_vor_16:30")


def test_outbound_after_time_window_kept():
    o = offer("outbound", [
        seg("STR", "DOH", "2027-05-14T16:35", "2027-05-14T23:30"),
        seg("DOH", "USM", "2027-05-15T02:00", "2027-05-15T11:30"),
    ])
    apply_constraints([o], cfg())
    assert not o.excluded, o.exclude_reason


def test_time_window_ignores_return_direction():
    o = offer("return", [
        seg("USM", "BKK", "2027-05-28T08:00", "2027-05-28T09:15"),
        seg("BKK", "STR", "2027-05-28T12:00", "2027-05-28T18:30"),
    ], search_date="2027-05-28")
    apply_constraints([o], cfg())
    assert not o.excluded, o.exclude_reason


def test_too_many_stops_excluded():
    o = offer("return", [
        seg("USM", "BKK", "2027-05-28T15:00", "2027-05-28T16:15"),
        seg("BKK", "DXB", "2027-05-28T18:00", "2027-05-28T21:30"),
        seg("DXB", "IST", "2027-05-28T23:30", "2027-05-29T03:30"),
        seg("IST", "STR", "2027-05-29T06:00", "2027-05-29T08:30"),
    ], search_date="2027-05-28")
    apply_constraints([o], cfg())
    assert o.excluded and o.exclude_reason.startswith("zu_viele_stopps")


def test_absurd_detour_excluded():
    # STR -> New York -> Koh Samui: Zwischenstopp liegt in die voellig falsche
    # Richtung (anderer Kontinent), obwohl formal nur 1 Stopp.
    o = offer("outbound", [
        seg("STR", "JFK", "2027-05-14T16:35", "2027-05-14T19:35"),
        seg("JFK", "USM", "2027-05-14T22:00", "2027-05-16T05:00"),
    ])
    apply_constraints([o], cfg())
    assert o.excluded and ("umweg" in o.exclude_reason or "rueckwaerts" in o.exclude_reason)


def test_short_layover_excluded():
    o = offer("outbound", [
        seg("STR", "DOH", "2027-05-14T16:35", "2027-05-14T23:30"),
        seg("DOH", "USM", "2027-05-14T23:50", "2027-05-15T09:00"),
    ])
    apply_constraints([o], cfg())
    assert o.excluded and o.exclude_reason.startswith("umstieg_zu_knapp")


def test_direct_feeder_not_overfiltered():
    o = offer("return", [seg("BKK", "USM", "2027-05-28T10:00", "2027-05-28T11:15")],
              price_total=1600.0, search_date="2027-05-28")
    apply_constraints([o], cfg())
    assert not o.excluded, o.exclude_reason


def test_multicity_stops_checked_per_leg_not_summed():
    # 2 Stopps hin + 2 Stopps zurueck = 4 gesamt, aber JEDE Richtung fuer
    # sich haelt das Limit (2) ein -> darf NICHT ausgeschlossen werden.
    out = [
        seg("STR", "IST", "2027-05-14T16:35", "2027-05-14T20:35"),
        seg("IST", "DOH", "2027-05-14T22:35", "2027-05-15T01:35"),
        seg("DOH", "USM", "2027-05-15T03:35", "2027-05-15T12:35"),
    ]
    ret = [
        seg("USM", "BKK", "2027-05-28T09:00", "2027-05-28T10:15"),
        seg("BKK", "DXB", "2027-05-28T12:30", "2027-05-28T15:30"),
        seg("DXB", "MUC", "2027-05-28T17:30", "2027-05-28T22:30"),
    ]
    o = mc_offer(out, ret, price_total=9000)
    apply_constraints([o], cfg())
    assert not o.excluded, o.exclude_reason


def test_multicity_stops_excluded_when_one_leg_too_long():
    out = [
        seg("STR", "IST", "2027-05-14T16:00", "2027-05-14T20:00"),
        seg("IST", "DOH", "2027-05-14T22:00", "2027-05-15T01:00"),
        seg("DOH", "SIN", "2027-05-15T03:00", "2027-05-15T12:00"),
        seg("SIN", "USM", "2027-05-15T14:00", "2027-05-15T15:00"),
    ]
    ret = [seg("USM", "MUC", "2027-05-28T09:00", "2027-05-28T18:00")]
    o = mc_offer(out, ret, price_total=9000)
    apply_constraints([o], cfg())
    assert o.excluded and o.exclude_reason.startswith("zu_viele_stopps")


def test_multicity_flight_time_checked_per_leg_not_summed():
    # 2x ~22h Flugzeit (Hin + Rueck) = 44h Summe, aber JEDE Richtung fuer sich
    # unter dem 40h-Limit -> darf nicht als "reisezeit_zu_lang" rausfliegen.
    out = [seg("STR", "USM", "2027-05-14T16:35", "2027-05-15T14:35")]   # 22h
    ret = [seg("USM", "MUC", "2027-05-28T09:00", "2027-05-29T07:00")]   # 22h
    o = mc_offer(out, ret, price_total=9000)
    apply_constraints([o], cfg())
    assert not o.excluded, o.exclude_reason


def test_austrian_excluded_by_default():
    o = offer("outbound", [
        seg("STR", "VIE", "2027-05-14T16:00", "2027-05-14T17:15", airline="Austrian"),
        seg("VIE", "USM", "2027-05-14T20:00", "2027-05-15T09:00", airline="Bangkok Airways"),
    ])
    o.airlines = ["Austrian", "Bangkok Airways"]
    apply_constraints([o], cfg())
    assert o.excluded and o.exclude_reason.startswith("airline_ausgeschlossen (Austrian)")


def test_gulf_carrier_kept_unless_switch_enabled():
    o = offer("outbound", [
        seg("STR", "DOH", "2027-05-14T16:35", "2027-05-14T23:30", airline="Qatar Airways"),
        seg("DOH", "USM", "2027-05-15T02:00", "2027-05-15T11:30", airline="Bangkok Airways"),
    ])
    o.airlines = ["Qatar Airways", "Bangkok Airways"]
    apply_constraints([o], cfg())
    assert not o.excluded, o.exclude_reason

    c2 = cfg().model_copy(deep=True)
    c2.flight_constraints.exclude_gulf_carriers = True
    apply_constraints([o], c2)
    assert o.excluded and "Qatar Airways" in o.exclude_reason


def test_multicity_time_window_applies_to_outbound_leg():
    out = [seg("STR", "USM", "2027-05-14T11:00", "2027-05-15T09:00")]  # vor 16:30
    ret = [seg("USM", "MUC", "2027-05-28T09:00", "2027-05-28T18:00")]
    o = mc_offer(out, ret, price_total=9000)
    apply_constraints([o], cfg())
    assert o.excluded and o.exclude_reason.startswith("abflug_vor_16:30")


def test_multicity_backwards_return_leg_is_caught():
    # Bugfix 11.09.26: die Geometrie-Pruefung (Umweg/rueckwaerts) nutzte bei
    # multi_city faelschlich off.route[0]/[-1] = Start-Heimatflughafen bis
    # Rueckkehr-Heimatflughafen (z.B. STR->MUC, ~190km) statt der echten
    # Langstrecke zum Ziel (USM) - direct_km lag dadurch IMMER unter
    # detour_min_direct_km (1500km), der Check war fuer JEDES Multi-City-
    # Angebot komplett inaktiv. Hier: Rueckflug USM->MUC absichtlich rueckwaerts
    # ueber Sydney geroutet (geografisch der falsche Kontinent) - muss jetzt
    # erkannt werden.
    out = [seg("STR", "USM", "2027-05-14T17:00", "2027-05-15T14:00")]
    ret = [
        seg("USM", "SYD", "2027-05-28T09:00", "2027-05-28T20:00"),
        seg("SYD", "MUC", "2027-05-28T23:00", "2027-05-29T18:00"),
    ]
    o = mc_offer(out, ret, price_total=9000)
    apply_constraints([o], cfg())
    # Egal ob Umweg- oder Rueckwaerts-Regel zuerst greift - Hauptsache es
    # wird ueberhaupt erkannt (vorher: nie, siehe Docstring oben) und korrekt
    # als Problem der RUECKstrecke markiert (nicht "hin").
    assert o.excluded
    assert o.exclude_reason.startswith(("umweg_rueck", "rueckwaerts_routing_rueck"))


def test_multicity_normal_routing_kept():
    # Gegenprobe: eine plausible Multi-City-Route (Hin ueber Doha, Rueck ueber
    # Bangkok) darf durch den gefixten Geometrie-Check NICHT faelschlich
    # rausfliegen.
    out = [
        seg("STR", "DOH", "2027-05-14T16:35", "2027-05-14T23:30"),
        seg("DOH", "USM", "2027-05-15T02:00", "2027-05-15T11:30"),
    ]
    ret = [
        seg("USM", "BKK", "2027-05-28T15:00", "2027-05-28T16:15"),
        seg("BKK", "MUC", "2027-05-28T18:30", "2027-05-29T01:00"),
    ]
    o = mc_offer(out, ret, price_total=9000)
    apply_constraints([o], cfg())
    assert not o.excluded, o.exclude_reason


def test_approximate_segment_times_skip_layover_minute_check():
    # flights_multicity_browser.py liefert echte Stopp-Zahl/Flughaefen, aber
    # nur gleichmaessig verteilte (synthetische) Segment-Zeiten - ein
    # minutengenauer Layover-Check darauf waere Zufall. 45 Min ist absichtlich
    # unter min_layover_minutes gewaehlt, damit sichergestellt ist, dass NUR
    # das Approximate-Flag den Check unterdrueckt (nicht Zufall).
    out = [
        seg("STR", "VIE", "2027-05-14T16:35", "2027-05-14T17:20"),
        seg("VIE", "BKK", "2027-05-14T17:40", "2027-05-15T00:20"),
        seg("BKK", "USM", "2027-05-15T00:40", "2027-05-15T14:05"),
    ]
    ret = [seg("USM", "MUC", "2027-05-28T09:00", "2027-05-28T18:00")]
    o = mc_offer(out, ret, price_total=9000)
    o.segment_times_approximate = True
    apply_constraints([o], cfg())
    assert not o.excluded, o.exclude_reason


def test_group_check_unconfirmed_excludes_even_though_otherwise_valid():
    # Eine 1-Pax-Hochrechnung, die von sources/flights.py collect_flights per
    # echter Gruppensuche widerlegt wurde (siehe group_check-Doku dort) - muss
    # ausgeschlossen werden, obwohl sie sonst alle Regeln erfuellt. Wichtig:
    # apply_constraints setzt excluded/exclude_reason bei jedem Lauf zurueck,
    # das Feld group_check_unconfirmed ueberlebt das (siehe offers.py).
    o = offer("outbound", [
        seg("STR", "DOH", "2027-05-14T16:00", "2027-05-14T23:30", airline="Qatar Airways"),
        seg("DOH", "USM", "2027-05-15T02:00", "2027-05-15T11:30", airline="Bangkok Airways"),
    ])
    o.group_check_unconfirmed = "gruppen_check: fuer 8 Personen nicht in diesem Preis verfuegbar"
    apply_constraints([o], cfg())
    assert o.excluded and o.exclude_reason.startswith("gruppen_check:")
