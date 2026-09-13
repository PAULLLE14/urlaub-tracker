"""Generischer Pauschalreise-Portal-Scraper.

Jedes Portal ist nur eine :class:`PortalSpec` (URL-Vorlage + Selektor-
Kandidaten). Die eigentliche Logik ist hier einmal und fuer alle gleich:
Seite laden, Consent weg, Ergebniskarten suchen, Preis + "Santiburi"-Treffer
extrahieren. Findet der Runner nichts, gibt es EIN ``ok=False``-Angebot mit
Screenshot - nie eine Exception nach oben.

WICHTIG: Die Portale sind schwere SPAs und aendern ihr Markup oft. Die
Selektor-Listen pro Portal sind bewusst grosszuegig und muessen gegen die
Live-Seite nachgezogen werden. Fuer Mai 2027 liefern die meisten Portale
(Stand jetzt) noch gar keine Ergebnisse - das ist erwartetes Verhalten.
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass, field

from ...config import Config
from ...logging_setup import get_logger
from ...offers import PackageOffer
from ..scraper_base import (
    browser_page,
    dismiss_consent,
    goto,
    parse_money,
    save_screenshot,
)

log = get_logger("source.package")


@dataclass
class PortalSpec:
    name: str
    operator: str
    search_url_template: str
    card_selectors: list[str] = field(default_factory=list)
    price_selectors: list[str] = field(default_factory=list)
    link_selector: str = "a"
    destination_query: str = "Koh Samui Santiburi"
    hotel_needle: str = "santiburi"
    extra_wait_ms: int = 4000


def _url(spec: PortalSpec, cfg: Config) -> str:
    t = cfg.trip
    return spec.search_url_template.format(
        checkin=t.outbound_dates[0].isoformat(),
        checkout=t.return_dates[-1].isoformat(),
        checkin_de=t.outbound_dates[0].strftime("%d.%m.%Y"),
        checkout_de=t.return_dates[-1].strftime("%d.%m.%Y"),
        persons=t.persons,
        rooms=t.rooms,
        nights=(t.return_dates[-1] - t.outbound_dates[0]).days or t.nights,
        destination=spec.destination_query.replace(" ", "+"),
    )


async def _extract_cards(page, spec: PortalSpec) -> list[dict]:
    for sel in spec.card_selectors:
        with contextlib.suppress(Exception):
            loc = page.locator(sel)
            n = await loc.count()
            if n:
                out = []
                for i in range(min(n, 15)):
                    card = loc.nth(i)
                    with contextlib.suppress(Exception):
                        text = (await card.inner_text(timeout=1000)).strip()
                        href = ""
                        with contextlib.suppress(Exception):
                            href = await card.locator(spec.link_selector).first.get_attribute(
                                "href", timeout=800
                            ) or ""
                        out.append({"text": text, "href": href, "selector": sel})
                if out:
                    return out
    return []


async def run_portal_search(cfg: Config, spec: PortalSpec) -> list[PackageOffer]:
    t = cfg.trip
    url = _url(spec, cfg)
    nights = (t.return_dates[-1] - t.outbound_dates[0]).days or t.nights
    base = dict(source=spec.name, currency=t.currency, operator=spec.operator,
                nights=nights, persons=t.persons, deep_link=url,
                hotel_name=cfg.trip.hotel.name)
    try:
        async with browser_page(cfg) as page:
            await goto(page, url)
            await dismiss_consent(page)
            await page.wait_for_timeout(spec.extra_wait_ms)

            cards = await _extract_cards(page, spec)
            offers: list[PackageOffer] = []
            for c in cards:
                low = c["text"].lower()
                is_hotel = spec.hotel_needle in low
                price = parse_money(_first_price_line(c["text"]))
                if price is None:
                    continue
                href = c["href"] or url
                if href.startswith("/"):
                    from urllib.parse import urljoin

                    href = urljoin(url, href)
                offers.append(PackageOffer(
                    ok=True, price_total=price, board="", dep_airport="",
                    error="" if is_hotel else "Hotelname nicht eindeutig 'Santiburi'",
                    raw={"card_selector": c["selector"], "hotel_match": is_hotel,
                         "snippet": c["text"][:280]},
                    **{**base, "deep_link": href},
                ))

            hotel_hits = [o for o in offers if o.raw.get("hotel_match")]
            chosen = hotel_hits or offers
            if chosen:
                log.info("%s: %d Angebote (%d mit Santiburi-Treffer)",
                         spec.name, len(offers), len(hotel_hits))
                return chosen

            shot = ""
            if cfg.sources.scraper.screenshot_on_error:
                shot = await save_screenshot(page, spec.name)
            log.info("%s: keine verwertbaren Ergebnisse (evtl. noch nicht buchbar)",
                     spec.name)
            return [PackageOffer(ok=False, price_total=None,
                                 error="keine Ergebnisse / Selektor pruefen",
                                 raw={"screenshot": shot}, **base)]
    except Exception as exc:  # noqa: BLE001
        log.warning("%s: Fehler %s: %s", spec.name, type(exc).__name__, exc)
        return [PackageOffer(ok=False, price_total=None,
                             error=f"{type(exc).__name__}: {exc}", **base)]


def _first_price_line(text: str) -> str:
    """Zeile mit Waehrungszeichen bevorzugen, sonst ganzen Text."""
    for line in text.splitlines():
        if any(sym in line for sym in ("€", "EUR", "CHF")):
            return line
    return text
