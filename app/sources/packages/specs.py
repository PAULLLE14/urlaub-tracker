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
    # Bugfix 17.09.26 (Nutzer-Fund: "TUI wird immer noch nicht gecheckt"):
    # die alte URL-Vorlage ("/pauschalreisen/suche/?hotelName=...") war
    # schlicht falsch geraten und lieferte reproduzierbar einen 404 - live
    # verifiziert. Die echte Hotel-Angebotsseite braucht TUIs eigene
    # numerische Hotel-ID (live gefunden ueber Google-Sitesuche
    # "site:tui.com Santiburi Koh Samui" -> Hotelseite -> "Termine & Preise"
    # -> "Hotel + Flug" geklickt, daraus die echte Such-URL abgelesen):
    #   https://www.tui.com/suchen/angebote/Santiburi-Koh-Samui/3866/offer/
    #     ?startDate=...&endDate=...&travellers=N&searchScope=PACKAGE
    # "travellers" ist die Personenzahl in EINEM Zimmer (kein Gruppenfeld) -
    # 8 direkt liefert reproduzierbar "keine Angebote" (kein Zimmer fasst 8).
    # max_persons_per_room aus der Config nutzen, wie bei den anderen
    # Zimmergroessen-Suchen (CHECK24 etc.) - liefert einen ehrlichen
    # Ein-Zimmer-Richtwert, KEINEN Preis fuer die volle Gruppe (Paket-Preise
    # lassen sich nicht einfach pro Zimmer aufsummieren wie Hotelpreise, das
    # wuerde den Flug mehrfach zaehlen - deshalb bewusst nicht wie CHECK24).
    # Live-Fund 17.09.26: fuer Mai 2027 kommt selbst mit korrekter URL noch
    # "keine Angebote" - TUI oeffnet Paketpreise offenbar erst deutlich
    # naeher am Reisedatum (siehe Modul-Docstring in portal_base.py). Die
    # URL ist trotzdem jetzt korrekt und greift automatisch, sobald TUI den
    # Zeitraum freischaltet.
    search_url_template=(
        "https://www.tui.com/suchen/angebote/Santiburi-Koh-Samui/3866/offer/"
        "?startDate={checkin}&endDate={checkout}&duration=default"
        "&travellers={max_room_persons}&searchScope=PACKAGE"
        "&showTotalPrice=0&jumpToFirstOffer=1"
    ),
    card_selectors=[
        "[data-testid='offer-card']",
        "article[class*='offer']",
        "li[class*='result']",
        "div[class*='hotelcard']",
        "div[class*='OfferCard']",
    ],
    price_selectors=["[data-testid='price']", "[class*='price']"],
    extra_wait_ms=6000,
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
