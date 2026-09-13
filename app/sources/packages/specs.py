"""Portal-Definitionen fuer die Pauschalreise-Scraper.

Nur Daten - keine Logik (die steckt in ``portal_base.run_portal_search``).
URL-Vorlagen nutzen Platzhalter: {checkin} {checkout} {checkin_de}
{checkout_de} {persons} {rooms} {nights} {destination}.

Alle URLs/Selektoren sind Startwerte und muessen gegen die Live-Seite
verifiziert/nachgezogen werden (die Portale sind SPAs mit haeufigen
Markup-Aenderungen).
"""
from __future__ import annotations

from .portal_base import PortalSpec

TUI = PortalSpec(
    name="tui",
    operator="TUI",
    search_url_template=(
        "https://www.tui.com/pauschalreisen/suche/?"
        "hotelName={destination}&von={checkin_de}&bis={checkout_de}"
        "&reisende=erwachsene:{persons}&zimmer={rooms}"
    ),
    card_selectors=[
        "[data-testid='offer-card']",
        "article[class*='offer']",
        "li[class*='result']",
        "div[class*='hotelcard']",
    ],
    price_selectors=["[data-testid='price']", "[class*='price']"],
)

DERTOUR = PortalSpec(
    name="dertour",
    operator="DERTOUR",
    search_url_template=(
        "https://www.dertour.de/suche?q={destination}"
        "&departureDate={checkin}&returnDate={checkout}"
        "&adults={persons}&rooms={rooms}"
    ),
    card_selectors=[
        "[data-testid='product-card']",
        "article[class*='result']",
        "div[class*='offer-item']",
    ],
    price_selectors=["[class*='price']", "[data-testid*='price']"],
)

ALLTOURS = PortalSpec(
    name="alltours",
    operator="alltours",
    search_url_template=(
        "https://www.alltours.de/suche/?searchterm={destination}"
        "&from={checkin}&to={checkout}&adults={persons}&rooms={rooms}"
    ),
    card_selectors=[
        "div.hotelbox",
        "[class*='hotel-result']",
        "article[class*='offer']",
    ],
    price_selectors=["[class*='preis']", "[class*='price']"],
)

REWE_REISEN = PortalSpec(
    name="rewe_reisen",
    operator="REWE Reisen",
    search_url_template=(
        "https://www.rewe-reisen.de/suche?query={destination}"
        "&start={checkin}&end={checkout}&adults={persons}&rooms={rooms}"
    ),
    card_selectors=[
        "[data-testid='offer']",
        "div[class*='result-card']",
        "article[class*='offer']",
    ],
    price_selectors=["[class*='price']", "[class*='preis']"],
)

SONNENKLAR = PortalSpec(
    name="sonnenklar",
    operator="sonnenklar.TV",
    search_url_template=(
        "https://www.sonnenklar.tv/suche?destination={destination}"
        "&departureDate={checkin}&duration={nights}"
        "&adults={persons}&rooms={rooms}"
    ),
    card_selectors=[
        "div.offer-item",
        "[class*='result-item']",
        "article[class*='hotel']",
    ],
    price_selectors=["[class*='price']", "[class*='preis']"],
)

ALL_SPECS = {
    "tui": TUI,
    "dertour": DERTOUR,
    "alltours": ALLTOURS,
    "rewe_reisen": REWE_REISEN,
    "sonnenklar": SONNENKLAR,
}
