"""Booking.com - Preis fuer das Santiburi, 3 Zimmer x 14 Naechte, 8 Gaeste.

Keine offizielle Affiliate-API vorhanden -> strukturiertes Scraping der
Property-Seite mit Playwright (JS-lastig). Rechtliche Grauzone: nur fuer die
eigene Preisanzeige, keine Weiterverbreitung. Bricht Booking das Layout oder
zeigt ein Anti-Bot-Captcha, wird ok=False + Screenshot geliefert, kein Crash.
"""
from __future__ import annotations

from urllib.parse import urlencode

from ...config import Config
from ...logging_setup import get_logger
from ...offers import HotelOffer
from ..scraper_base import (
    all_prices_on_page,
    browser_page,
    dismiss_consent,
    first_text,
    goto,
    parse_money,
    save_screenshot,
)

log = get_logger("source.booking")
SOURCE = "booking"

_TOTAL_SELECTORS = [
    "[data-testid='recommended-units-total-price']",
    "[data-testid='price-and-discounted-price']",
    ".bui-price-display__value",
    ".prco-valus-container",
    "[data-testid='total-price'] span",
]
_ROOM_PRICE_SELECTORS = [
    "[data-testid='price-and-discounted-price']",
    "td.hprt-table-cell-price .prco-valus",
    ".hprt-price-price-standard",
    "span.prco-inline-block-maker-helper",
]


def _build_url(cfg: Config) -> str:
    t = cfg.trip
    base = t.hotel.booking_url or "https://www.booking.com/searchresults.html"
    params = {
        "checkin": t.hotel_checkin.isoformat(),
        "checkout": t.hotel_checkout.isoformat(),
        "group_adults": t.persons,
        "no_rooms": t.rooms,
        "group_children": 0,
        "selected_currency": t.currency,
        "lang": "de",
    }
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}{urlencode(params)}"


async def fetch(cfg: Config) -> HotelOffer:
    t = cfg.trip
    url = _build_url(cfg)
    nights = (t.hotel_checkout - t.hotel_checkin).days or t.nights
    offer = HotelOffer(source=SOURCE, ok=False, price_total=None,
                       currency=t.currency, nights=nights, rooms=t.rooms,
                       guests=t.persons, deep_link=url,
                       room_desc=t.hotel.room_type_hint)
    try:
        async with browser_page(cfg) as page:
            await goto(page, url)
            await dismiss_consent(page)
            await page.wait_for_timeout(2500)

            txt, sel = await first_text(page, _TOTAL_SELECTORS)
            total = parse_money(txt)
            if total:
                offer.raw["match"] = {"selector": sel, "text": txt}
            else:
                prices = sorted(await all_prices_on_page(page, _ROOM_PRICE_SELECTORS))
                if len(prices) >= t.rooms:
                    total = round(sum(prices[: t.rooms]), 2)
                    offer.raw["match"] = {"strategy": f"{t.rooms} guenstigste Zimmer",
                                          "prices": prices[:6]}

            if total and total > 0:
                offer.ok = True
                offer.price_total = total
                offer.per_night = round(total / nights, 2) if nights else None
                log.info("%s: Gesamtpreis %.2f %s (%d Naechte)", SOURCE, total,
                         t.currency, nights)
            else:
                offer.error = "kein Preis auf der Seite gefunden (Layout/Anti-Bot?)"
                if cfg.sources.scraper.screenshot_on_error:
                    offer.raw["screenshot"] = await save_screenshot(page, SOURCE)
                log.warning("%s: %s", SOURCE, offer.error)
    except Exception as exc:  # noqa: BLE001
        offer.error = f"{type(exc).__name__}: {exc}"
        log.warning("%s: Fehler %s", SOURCE, offer.error)
    return offer
