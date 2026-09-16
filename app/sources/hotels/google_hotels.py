"""Hotelpreis via Google Hotels (Server-HTML) - die "schlaue" Loesung.

Statt Booking/Expedia einzeln zu scrapen (Anti-Bot, brechen staendig), holen
wir EINE Google-Hotels-Seite fuer genau dieses Hotel. Google aggregiert dort
~20 OTAs (Booking, Expedia, Agoda, Hotels.com, Trip.com, CHECK24, HolidayCheck,
TUI ...) inklusive Zimmertypen mit Belegung - in einem Request, gleiche
robuste Masche wie bei den Fluegen (primp/httpx + Consent-Cookie ``SOCS=CAI``,
kein Browser noetig).

Fuer Mai 2027 liefert Google oft nur einen 1-Nacht-Richtwert (Datum ausserhalb
des Buchungsfensters). Das wird als ``estimate`` markiert; der Wert konvergiert,
sobald die Hotels den Zeitraum oeffnen.

price_total = Villa-Preis/Nacht (>=3 Gaeste) x rooms x nights; sonst
guenstigster OTA-Nachtpreis x rooms x nights (dann als Basiszimmer-Schaetzung
markiert).
"""
from __future__ import annotations

import re
from urllib.parse import quote_plus

from ...config import Config
from ...logging_setup import get_logger
from ...offers import HotelOffer
from ..scraper_base import parse_money

log = get_logger("source.google_hotels")
SOURCE = "google_hotels"

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
_EUR = r"(?:€|EUR)"
_GUESTS = re.compile(r"(\d)\s*(?:Gäste|Gast|guests?|adults?)", re.I)
# Bekannte OTA-Anzeigenamen (Google Hotels ist mehrsprachig, Namen stabil).
# Nutzer-Fund 16.09.26 (Screenshot der "Alle Optionen"-Ansicht fuer genau
# dieses Hotel): DERTOUR, Stayforlong.de, Halalbooking, EaseMyTrip.com,
# ZenHotels.com und weloveholidays tauchten dort real auf, fehlten aber in
# dieser Liste - der Regex-Scan in _parse() haette sie schlicht ignoriert,
# obwohl sie teils guenstiger waren als die bisher erfassten OTAs.
_OTA_NAMES = ("Booking.com", "Expedia.de", "Expedia", "Agoda", "Hotels.com",
              "Trip.com", "CHECK24.de", "HolidayCheck.de", "TUI.com", "DERTOUR",
              "Opodo", "Kiwi.com", "Priceline", "KAYAK.de", "eDreams", "ebookers",
              "Destinia", "Vio.com", "Wego", "Etrip.net", "Bluepillow.de",
              "klook", "Mytrip", "easyJet holidays", "hutchgo.de",
              "Stayforlong.de", "Halalbooking", "EaseMyTrip.com", "ZenHotels.com",
              "weloveholidays", "lastminute.com", "Tripado", "Travomint")


def _url(cfg: Config) -> str:
    t = cfg.trip
    return (f"https://www.google.com/travel/search?q={quote_plus(t.hotel.name)}"
            f"&checkin={t.hotel_checkin.isoformat()}"
            f"&checkout={t.hotel_checkout.isoformat()}"
            f"&curr={t.currency}&hl=de")


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


def _visible_text(html: str) -> str:
    try:
        from selectolax.lexbor import LexborHTMLParser

        return LexborHTMLParser(html).text()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)


def _parse(text: str) -> dict:
    otas: dict[str, float] = {}
    for name in _OTA_NAMES:
        m = re.search(r"%s[^€]{0,60}?%s\s?(\d[\d.,]{1,6})"
                      % (re.escape(name), _EUR), text)
        if not m:
            continue
        val = parse_money(m.group(1))
        if val and 20 <= val <= 20000:
            otas.setdefault(name, val)

    rooms: list[dict] = []
    for m in re.finditer(
        r"([A-Z][A-Za-z0-9 \-]{2,44}?(?:Villa|Suite|Zimmer|Room|Bungalow|Cottage|Residence|Pool))"
        r"[^€]{0,90}?%s\s?(\d[\d.,]{1,6})" % _EUR, text
    ):
        price = parse_money(m.group(2))
        if not price:
            continue
        gm = _GUESTS.search(m.group(0))
        name = re.sub(r"^(?:Zur\s+Website|Website|Zur\s+Webseite|Visit site)\s*",
                      "", m.group(1).strip())
        rooms.append({"name": name,
                      "guests": int(gm.group(1)) if gm else None,
                      "per_night": price})

    hist: dict[str, float] = {}
    hm = re.search(r"%s\s?(\d[\d.,]{1,6})\s*(?:ist\s+niedrig|is\s+low)" % _EUR, text)
    if hm:
        hist["low"] = parse_money(hm.group(1))
    # "... EUR166 EUR292 EUR430" (niedrig / typisch / hoch) im Historie-Widget
    tri = re.search(r"%s\s?(\d{2,4})\D{0,12}%s\s?(\d{2,4})\D{0,12}%s\s?(\d{2,4})\D{0,40}"
                    r"(?:Preisverlauf|price history)" % (_EUR, _EUR, _EUR), text)
    if tri:
        lo, mid, hi = (parse_money(x) for x in tri.groups())
        hist.update(low=hist.get("low") or lo, typical=mid, high=hi)

    return {"otas": otas, "rooms": rooms, "history": hist,
            "one_night": bool(re.search(r"\b1\s*(?:Nacht|night)\b", text))}


def fetch(cfg: Config, proxy: str | None = None) -> HotelOffer:
    t = cfg.trip
    nights = (t.hotel_checkout - t.hotel_checkin).days or t.nights
    url = _url(cfg)
    offer = HotelOffer(source=SOURCE, ok=False, price_total=None,
                       currency=t.currency, nights=nights, rooms=t.rooms,
                       guests=t.persons, deep_link=url,
                       room_desc=t.hotel.room_type_hint)
    try:
        html = _fetch_html(url, proxy)
        data = _parse(_visible_text(html))

        # Ehrliche Einordnung: Google Hotels SSR ignoriert die Belegung in der
        # URL und liefert Nachtpreise fuer das *guenstigste* Zimmer (meist 2
        # Pers.), nicht fuer 3 Villen / 8 Pers. Wir nehmen daher den
        # guenstigsten sichtbaren Nachtpreis (OTA-Zeile ODER Zimmertyp-Zeile)
        # als FLOOR-Richtwert. Fuer den echten "3 Villen / 8 Pers."-Preis:
        # config.trip.reference_offers pflegen (z.B. CHECK24-Fund).
        ota_min = min(data["otas"].values()) if data["otas"] else None
        room_prices = [r["per_night"] for r in data["rooms"] if r["per_night"]]
        room_min = min(room_prices) if room_prices else None
        villa3 = [r["per_night"] for r in data["rooms"]
                  if r["per_night"] and r["guests"] and r["guests"] >= t.max_persons_per_room]

        cands = [x for x in (ota_min, room_min) if x]
        per_night = min(cands) if cands else data["history"].get("low")

        offer.raw = {
            "otas": data["otas"], "rooms": data["rooms"], "history": data["history"],
            "estimate": data["one_night"],
            "villa_min_per_night": min(villa3) if villa3 else None,
            "basis": ("guenstigster sichtbarer Nachtpreis x Zimmer x Naechte - "
                      "Belegung/3-Pers.-Villa NICHT beruecksichtigt (Floor-Richtwert; "
                      "die CHECK24-Quelle sucht die Belegung korrekt und wird "
                      "bevorzugt, wenn sie erfolgreich war)"),
        }

        if per_night and per_night > 0:
            offer.ok = True
            offer.per_night = round(per_night, 2)
            offer.price_total = round(per_night * t.rooms * nights, 2)
            if data["one_night"]:
                offer.error = ("Google zeigt fuer den Zeitraum nur 1-Nacht-Preise "
                               "(Mai 2027 noch nicht buchbar) - Floor-Richtwert")
            log.info("%s: %.0f/Nacht (Floor) -> gesamt %.0f %s%s%s", SOURCE, per_night,
                     offer.price_total, t.currency,
                     " [1N-Richtwert]" if data["one_night"] else "",
                     f" | Villa>=3 ab {min(villa3):.0f}" if villa3 else "")
        else:
            offer.error = "keine Preise aus Google Hotels lesbar (Layout?)"
            log.warning("%s: %s (html %d B)", SOURCE, offer.error, len(html))
    except Exception as exc:  # noqa: BLE001
        offer.error = f"{type(exc).__name__}: {exc}"
        log.warning("%s: Fehler %s", SOURCE, offer.error)
    return offer
