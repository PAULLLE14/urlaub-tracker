import base64
from datetime import date

from app.sources.hotels.google_hotels import _build_ts, _url


def _decode_varint(data: bytes, i: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        b = data[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7


def _decode_fields(data: bytes) -> list[tuple[int, int, object]]:
    """Minimaler Protobuf-Decoder: liefert (field_num, wiretype, value)."""
    out = []
    i = 0
    while i < len(data):
        tag, i = _decode_varint(data, i)
        field_num, wiretype = tag >> 3, tag & 0x7
        if wiretype == 0:
            val, i = _decode_varint(data, i)
        elif wiretype == 2:
            length, i = _decode_varint(data, i)
            val = data[i:i + length]
            i += length
        else:
            raise AssertionError(f"unerwarteter wiretype {wiretype}")
        out.append((field_num, wiretype, val))
    return out


def _find_dates(data: bytes) -> list[tuple[int, int, int]]:
    """Sucht rekursiv alle 3-Feld-Nachrichten mit Werten wie ein Datum
    (Jahr 2000-2100, Monat 1-12, Tag 1-31) - robust gegen die genaue
    Verschachtelungstiefe, prueft nur dass die Datumswerte irgendwo drinstecken."""
    found = []
    for _, wiretype, val in _decode_fields(data):
        if wiretype == 2 and isinstance(val, (bytes, bytearray)):
            fields = None
            try:
                fields = _decode_fields(bytes(val))
            except Exception:
                pass
            if fields and len(fields) == 3 and all(wt == 0 for _, wt, _ in fields):
                y, m, d = (v for _, _, v in fields)
                if 2000 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31:
                    found.append((y, m, d))
            if fields:
                found += _find_dates(bytes(val))
    return found


def test_build_ts_encodes_requested_checkin_and_checkout_dates():
    # Live-Fund 17.09.26: Google Hotels ignoriert simple checkin=/checkout=
    # Query-Parameter; das echte Datum steckt im ts=-Blob. Hier per
    # unabhaengigem Mini-Decoder verifiziert, statt die Bytes blind zu
    # vertrauen (live im Browser bereits gegengeprueft: Google zeigte nach
    # diesem Blob korrekt "Sa., 15. Mai" / "Fr., 28. Mai" an).
    ts = _build_ts(date(2027, 5, 15), date(2027, 5, 28), adults=8, currency="EUR")
    raw = base64.urlsafe_b64decode(ts + "=" * (-len(ts) % 4))
    dates = _find_dates(raw)
    assert (2027, 5, 15) in dates
    assert (2027, 5, 28) in dates


def test_build_ts_encodes_adult_count():
    # 8 "Erwachsenen"-Eintraege (Kategorie-Code 3) muessen im Blob stecken -
    # die Web-UI deckelt den Gaeste-Stepper zwar bei 6, das direkt gebaute
    # ts= unterliegt dieser Beschraenkung aber nicht (live verifiziert).
    ts = _build_ts(date(2027, 5, 15), date(2027, 5, 28), adults=8, currency="EUR")
    raw = base64.urlsafe_b64decode(ts + "=" * (-len(ts) % 4))
    adult_entries = raw.count(bytes([0x0A, 0x02, 0x08, 0x03]))
    assert adult_entries == 8


def test_url_uses_ts_param_not_ignored_checkin_checkout_params():
    from types import SimpleNamespace

    cfg = SimpleNamespace(trip=SimpleNamespace(
        hotel=SimpleNamespace(name="Santiburi Koh Samui"),
        hotel_checkin=date(2027, 5, 15), hotel_checkout=date(2027, 5, 28),
        persons=8, currency="EUR",
    ))
    url = _url(cfg)
    assert "ts=" in url
    # Bugfix 17.09.26: fruehere Version nutzte checkin=/checkout=, die
    # Google clientseitig ignoriert - die duerfen hier nicht mehr auftauchen.
    assert "checkin=" not in url
    assert "checkout=" not in url


def test_extract_providers_reads_price_total_and_direct_link():
    from app.sources.hotels.google_hotels import extract_providers

    html = (
        '[["Trip.com",998605,"/travel/lodging/clk?pc\u003dABC\u0026pcurl\u003d'
        'https://de.trip.com/hotels/redirect?hotelid%3D1%26x%3D2\u0026ap\u003d1",[],0],'
        'null,null,["170 €"],["2.212 €"],null],'
        '[["Booking.com",1234,"/travel/lodging/clk?pc\u003dDEF\u0026pcurl\u003d'
        'https://www.booking.com/searchresults.de.html\u0026ap\u003d1",[],0],'
        'null,["187 €"],["2.435 €"],null]'
    )
    res = extract_providers(html)
    assert [p.name for p in res] == ["Trip.com", "Booking.com"]     # nach Gesamtpreis sortiert
    assert res[0].total == 2212.0 and res[0].per_night == 170.0
    assert res[0].url.startswith("https://de.trip.com/hotels/redirect")
    assert res[1].total == 2435.0


def test_extract_providers_reads_sponsored_expedia_block():
    from app.sources.hotels.google_hotels import extract_providers

    html = ('<div class="ADs2Tc" data-id="j2tiVc_Expedia.de"><a href="/aclk?sa=l&amp;ai=XYZ">'
            '<span>170 €</span><span>170 €</span><span>2.215 €</span></a></div>')
    res = extract_providers(html)
    assert len(res) == 1 and res[0].name == "Expedia.de"
    assert res[0].total == 2215.0 and res[0].per_night == 170.0
    assert res[0].url == "https://www.google.com/aclk?sa=l&ai=XYZ"
