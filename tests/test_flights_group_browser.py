import asyncio

import app.sources.flights_group_browser as gb
from app.config import get_config
from tests.helpers import rt_offer, seg

SAMPLE = """Suchergebnisse
Beste Flüge
Am günstigsten
Hinflüge
Preise beinhalten erforderliche Steuern und Gebühren für 8 Erwachsene.
Nach beliebtesten Flügen sortiert
17:15
 –
16:30+1
Qatar Airways, Bangkok Airways
18 Std. 15 Min.
FRA–USM
2 Stopps
DOH, BKK
6.051 kg CO2e
+9 % (geschätzt)
7.939 €
Hin und zurück
21:10
 –
16:30+1
Condor, Bangkok Airways
14 Std. 20 Min.
FRA–USM
1 Stopp
1 Std. 35 Min. BKK
4.553 kg CO2e
-18 % (geschätzt)
9.200 €
Hin und zurück
Mehr Flüge ansehen"""


def test_parses_all_real_roundtrip_prices():
    # Live-Fund 13.09.26: primp sah bei dieser Suche nur den Condor-Preis
    # (9200), der echte Browser zeigt sofort BEIDE inkl. dem guenstigeren
    # Qatar-Preis (7939) - dieser Test haelt genau diese Rohdaten-Erkennung fest.
    prices = gb._parse_rt_prices(SAMPLE)
    assert sorted(prices) == [7939.0, 9200.0]


def test_verify_picks_cheapest_split_shape(monkeypatch):
    # 13.09.26 Nutzervorgabe: nicht nur 4+4, auch 4+2+2 und 2+2+2+2 pruefen -
    # hier ist 4+2+2 (2500+2x1000=4500) guenstiger als 4+4 (2x2500=5000) und
    # 2+2+2+2 (4x1000=4000 waere sogar noch billiger - also MUSS 2+2+2+2
    # gewinnen). Kein echter Browser-Aufruf: _cheapest_real_price gemockt.
    prices = {2: 1000.0, 4: 2500.0, 8: 9999.0}

    async def fake_price(cfg, origin, out_d, ret_d, pax):
        return prices.get(pax), f"https://example.test/{pax}"

    monkeypatch.setattr(gb, "_cheapest_real_price", fake_price)

    cfg = get_config()
    est = rt_offer("FRA", [
        seg("FRA", "USM", "2027-05-14T17:00", "2027-05-15T09:00", airline="Lufthansa"),
    ], price_total=8000.0)
    offers = [est]

    health = asyncio.run(gb.verify(cfg, offers))

    split_offers = [o for o in offers if o.pax_mode.startswith("split_")]
    assert split_offers, "keine Split-Angebote erzeugt"
    cheapest_split = min(split_offers, key=lambda o: o.price_total)
    assert cheapest_split.pax_mode == "split_2_2_2_2"
    assert cheapest_split.price_total == 4000.0
    assert health["split_checked"] > 0
