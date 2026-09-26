"""Hotelpreise ALLER Anbieter via Google Hotels (Server-HTML) - Vergleichsquelle.

Google Hotels zeigt fuer das Hotel ~10-20 Anbieter (Booking, Expedia, Agoda,
Trip.com, CHECK24, TUI, DERTOUR, Opodo, Hotels.com ...) mit dem Preis pro Nacht
UND dem Gesamtpreis fuer den Aufenthalt, jeweils INKL. Steuern und Gebuehren,
dazu den Direktlink des Anbieters mit Datum/Personen. Alles steckt als JSON im
Server-HTML (kein Browser noetig, primp/httpx + Consent-Cookie ``SOCS=CAI``).

Live-Fund 26.09.26: mit dem ``ts=``-Blob fuer 15.-28.05.2027 und 2 Erwachsene
liefert Google echte Preise (vorher mit 8 Gaesten: keine - kein Zimmer fasst
8). Deshalb wie bei CHECK24/Santiburi je Zimmergroesse einzeln (1 Zimmer, N
Erwachsene) abgefragt und je Anbieter zur guenstigsten Aufteilung summiert.
"""
from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass
from datetime import date
from urllib.parse import parse_qs, quote_plus, urlparse

from ...config import Config
from ...logging_setup import get_logger
from ...offers import HotelOffer
from ..room_split import candidate_allocations, cheapest_allocation, explicit_allocations
from ..scraper_base import parse_money

log = get_logger("source.google_hotels")
SOURCE = "google_hotels"

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _field_varint(num: int, val: int) -> bytes:
    return _varint((num << 3) | 0) + _varint(val)


def _field_bytes(num: int, payload: bytes) -> bytes:
    return _varint((num << 3) | 2) + _varint(len(payload)) + payload


def _date_msg(y: int, m: int, d: int) -> bytes:
    return _field_varint(1, y) + _field_varint(2, m) + _field_varint(3, d)


def _build_ts(checkin, checkout, adults: int, currency: str) -> str:
    """Baut den ``ts=``-Parameter, den Google Hotels intern fuer Datum +
    Belegung nutzt (ein Protobuf-Blob, base64url-codiert).

    Live-Fund 17.09.26 (Nutzer-Screenshot: Deep-Link zeigte "9.-10. Nov, 2
    Gaeste" statt Mai 2027/8 Pers.): die einfachen ``checkin=``/``checkout=``
    Query-Parameter werden von Googles Client-JS komplett ignoriert - der
    echte Suchzustand steckt in diesem ``ts=``-Blob. Struktur live per
    Browser-Interaktion (Datum-Picker + Gaeste-Stepper auf der echten Seite)
    reverse-engineered und byteweise verifiziert (siehe Analyse-Notizen im
    zugehoerigen Commit). Mit konstruiertem ``ts=`` fuer 8 Erwachsene live
    getestet: Google zeigt korrekt "Sa., 15. Mai" / "Fr., 28. Mai" / "8" an
    (die Web-UI selbst deckelt den Gaeste-Stepper bei 6, das direkt gebaute
    ``ts=`` unterliegt dieser UI-Beschraenkung nicht).
    """
    adults_content = bytearray()
    for _ in range(adults):
        adults_content += _field_bytes(1, _field_varint(1, 3))
    adults_content += _field_varint(3, 1)
    field2 = _field_bytes(2, bytes(adults_content))

    date_range = (_field_bytes(1, _date_msg(checkin.year, checkin.month, checkin.day))
                  + _field_bytes(2, _date_msg(checkout.year, checkout.month, checkout.day)))
    level2 = _field_bytes(2, date_range) + _field_bytes(6, _field_varint(1, 0))
    level1 = _field_bytes(1, _field_bytes(3, b"")) + _field_bytes(2, level2)
    field3 = _field_bytes(3, level1)

    curr_bytes = currency.encode("ascii")
    curr = _field_bytes(1, _field_bytes(7, curr_bytes)) + _field_bytes(3, b"")
    field5 = _field_bytes(5, curr)

    top = _field_varint(1, 1) + field2 + field3 + field5 + _field_bytes(3, b"")
    return base64.urlsafe_b64encode(bytes(top)).rstrip(b"=").decode("ascii")


def _url_for(cfg: Config, checkin: date, checkout: date, adults: int) -> str:
    t = cfg.trip
    ts = _build_ts(checkin, checkout, adults, t.currency)
    return (f"https://www.google.com/travel/search?q={quote_plus(t.hotel.name)}"
            f"&curr={t.currency}&hl=de&ts={ts}")


def _url(cfg: Config) -> str:
    t = cfg.trip
    return _url_for(cfg, t.hotel_checkin, t.hotel_checkout, min(t.persons, 8))


def _fetch_html(url: str, proxy: str | None) -> str:
    try:
        import primp

        c = primp.Client(impersonate="chrome_146", impersonate_os="windows",
                         cookie_store=True, follow_redirects=True, timeout=40,
                         proxy=proxy)
        try:
            c.set_cookies("https://www.google.com", {"SOCS": "CAI"})
        except Exception:
            pass
        return c.get(url).text
    except Exception:
        import httpx

        with httpx.Client(follow_redirects=True, timeout=40, proxy=proxy,
                          headers={"User-Agent": _UA,
                                   "Accept-Language": "de-DE,de;q=0.9"},
                          cookies={"SOCS": "CAI"}) as c:
            return c.get(url).text


_PROVIDER_ENTRY = re.compile(r'\["([^"\\]{2,40})",(\d+),"(/travel/lodging/clk[^"]*)"')
_PRICE_PAIR = re.compile(r'\["([\d.,]+)\s?[€]"\],\["([\d.,]+)\s?[€]"\]')


_MAX_ATTEMPTS = 4
_ENOUGH_PROVIDERS = 10
_SPONSORED_BLOCK = re.compile(r'data-id="j2tiVc_([^"]+)"')
_SPONSORED_PRICE = re.compile(r'>([\d.,]+\s?€)<')
_SPONSORED_LINK = re.compile(r'href="(/aclk[^"]+)"')


@dataclass
class ProviderPrice:
    name: str
    per_night: float       # inkl. Steuern und Gebuehren
    total: float           # Gesamtpreis fuer den Aufenthalt, inkl. Steuern/Gebuehren
    url: str               # Direktlink des Anbieters (Datum/Personen enthalten)


def extract_providers(html: str) -> list[ProviderPrice]:
    """Liest die Anbieterzeilen aus dem in Googles Server-HTML eingebetteten
    JSON: ``["Name",id,"/travel/lodging/clk?...pcurl=<Direktlink>..."]`` gefolgt
    von ``["170 €"],["2.212 €"]`` (Preis/Nacht, Gesamtpreis, beide inkl. Steuern).
    Je Anbieter zaehlt die guenstigste Zeile."""
    entries = list(_PROVIDER_ENTRY.finditer(html))
    best: dict[str, ProviderPrice] = {}
    for k, m in enumerate(entries):
        end = entries[k + 1].start() if k + 1 < len(entries) else m.end() + 3500
        seg = html[m.end():min(end, m.end() + 3500)]
        pm = _PRICE_PAIR.search(seg)
        if not pm:
            continue
        per_night, total = parse_money(pm.group(1)), parse_money(pm.group(2))
        if not per_night or not total:
            continue
        try:
            raw_url = json.loads('"' + m.group(3) + '"')
        except ValueError:
            raw_url = m.group(3)
        target = parse_qs(urlparse(raw_url).query).get("pcurl", [""])[0]
        url = target or ("https://www.google.com" + raw_url)
        name = m.group(1)
        if name not in best or total < best[name].total:
            best[name] = ProviderPrice(name, per_night, total, url)

    # Gesponserte "Vorgestellte Optionen" (z.B. Expedia.de) stehen NICHT im
    # JSON oben, sondern nur als HTML-Block mit Werbe-Klicklink (/aclk?...).
    blocks = list(_SPONSORED_BLOCK.finditer(html))
    for k, m in enumerate(blocks):
        end = blocks[k + 1].start() if k + 1 < len(blocks) else m.end() + 4000
        seg = html[m.end():min(end, m.end() + 4000)]
        prices = [parse_money(x) for x in _SPONSORED_PRICE.findall(seg)]
        if len(prices) < 3 or not prices[0] or not prices[2]:
            continue
        name = m.group(1)
        link = _SPONSORED_LINK.search(seg)
        url = ("https://www.google.com" + link.group(1).replace("&amp;", "&")) if link else ""
        if name not in best or prices[2] < best[name].total:
            best[name] = ProviderPrice(name, prices[0], prices[2], url)
    return sorted(best.values(), key=lambda p: p.total)


def fetch_providers(cfg: Config, checkin: date, checkout: date,
                    proxy: str | None = None) -> list[HotelOffer]:
    """Ein HotelOffer je Anbieter fuer genau diesen Zeitraum (Summe ueber die
    guenstigste Zimmeraufteilung fuer alle Personen). Anbieter, die nicht fuer
    alle noetigen Zimmergroessen einen Preis haben, fallen heraus."""
    t = cfg.trip
    nights = (checkout - checkin).days
    allocations = (explicit_allocations(t.hotel.allowed_room_shapes, t.persons)
                   if t.hotel.allowed_room_shapes
                   else candidate_allocations(t.persons, t.max_persons_per_room))
    sizes = sorted({size for a in allocations for size in a})
    per_size: dict[int, dict[str, ProviderPrice]] = {}
    for size in sizes:
        url = _url_for(cfg, checkin, checkout, size)
        # Live-Fund 26.09.26: Google liefert vom Server je Abruf nur 2-6 von ~12
        # Anbietern (lokal 12) - mehrfach abrufen und pro Anbieter den besten
        # Preis behalten, bis genug Anbieter beisammen sind.
        merged: dict[str, ProviderPrice] = {}
        for attempt in range(_MAX_ATTEMPTS):
            if attempt:
                time.sleep(3)
            try:
                found = extract_providers(_fetch_html(url, proxy))
            except Exception as exc:  # noqa: BLE001
                log.warning("%s: 1 Zimmer/%d Erw. Versuch %d fehlgeschlagen: %s",
                            SOURCE, size, attempt + 1, exc)
                continue
            for p in found:
                if p.name not in merged or p.total < merged[p.name].total:
                    merged[p.name] = p
            if len(merged) >= _ENOUGH_PROVIDERS:
                break
        if not merged:
            continue
        per_size[size] = merged
        log.info("%s: 1 Zimmer/%d Erw. %s->%s: %d Anbieter", SOURCE, size, checkin,
                 checkout, len(per_size[size]))

    names = sorted({n for d in per_size.values() for n in d})
    offers: list[HotelOffer] = []
    for name in names:
        price_per_size = {s: per_size[s][name].total for s in sizes
                          if name in per_size.get(s, {})}
        best = cheapest_allocation(allocations, price_per_size)
        if not best:
            continue
        counts, total = best
        info = {str(s): {"url": per_size[s][name].url, "cheapest": per_size[s][name].total,
                         "per_night": per_size[s][name].per_night}
                for s in sizes if name in per_size.get(s, {})}
        offers.append(HotelOffer(
            source=f"{name} (Google)", ok=True, price_total=round(total, 2),
            currency=t.currency, nights=nights, rooms=sum(counts.values()),
            guests=t.persons, per_night=round(total / nights, 2) if nights else None,
            deep_link=next(iter(info.values()))["url"],
            room_desc=t.hotel.room_type_hint,
            raw={"provider": name, "via": "Google Hotels",
                 "checkin": checkin.isoformat(), "checkout": checkout.isoformat(),
                 "room_split": {str(k): v for k, v in counts.items()},
                 "per_room_size": info, "all_in": True,
                 "basis": "Preis inkl. Steuern und Gebuehren laut Google Hotels, "
                          "je Zimmergroesse einzeln gesucht, guenstigste Aufteilung"},
        ))
    offers.sort(key=lambda o: o.price_total)
    return offers
