"""ITA Matrix (matrix.itasoftware.com, Googles eigenes Fare-Research-Tool).

WICHTIG: ITA Matrix ist KEINE Buchungsseite - man kann dort keinen Flug
kaufen, nur Tarife/Routings recherchieren, die die grossen Suchmaschinen
(auch unser eigener Google-Flights-Scraper) manchmal nicht zeigen. Deshalb
laeuft das hier bewusst als eigene "Recherche-Referenz"-Kategorie, NIE
vermischt mit den buchbaren Angeboten aus sources/flights.py.

Deep-Link-Mechanik (wie CHECK24/Santiburi/lastminute): kein Formular-Ausfuellen
noetig, sondern ein base64-kodiertes JSON direkt in der URL:

    https://matrix.itasoftware.com/flights?search=<base64(JSON)>

JSON-Form (per Playwright-Suche in der eigenen UI ermittelt, 11.09.26):
    {"type": "round-trip",
     "slices": [{"origin": ["FRA"], "dest": ["USM"],
                 "dates": {"searchDateType": "specific",
                           "departureDate": "2027-05-14", "departureDateType": "depart",
                           "departureDateModifier": "0", "departureDatePreferredTimes": [],
                           "returnDate": "2027-05-28", "returnDateType": "depart",
                           "returnDateModifier": "0", "returnDatePreferredTimes": []}}],
     "options": {"cabin": "COACH", "stops": "-1", "extraStops": "1",
                 "allowAirportChanges": "true", "showOnlyAvailable": "true"},
     "pax": {"adults": "1"}}

Wichtige Erkenntnis aus dem Live-Test: ITA Matrix rechnet deutlich langsamer
als Google Flights (haeufig 25-40s, bei mehreren Passagieren auch >60s ohne
klares oberes Limit) - eine 1-Pax-Suche mit Hochrechnung x Personenzahl ist
daher zuverlaessiger als eine echte Gruppensuche (die Preisspalte zeigt bei
1 Pax ohnehin den Preis pro Person, exakt wie bei unserem Google-Flights-Weg).
Fuer alle, die die Original-Seite trotzdem selbst mit echter Personenzahl
aufrufen wollen, wird zusaetzlich ein entsprechender Deep-Link mitgeliefert.

Bugfix 14.09.26 (Nutzer-Fund): der Standard-Tab nach dem Laden ("Complete
Trips") berechnet fuer FRA-USM offenbar nie ein komplettes Routing und
bleibt leer - das wurde zuvor faelschlich als "keine Daten fuer diese Route"
interpretiert. Der "Individual Flights"-Tab zeigt dagegen eine echte
Preis-Matrix (Airline x Stopp-Zahl) je Einzelstrecke. ``_search_one`` klickt
jetzt dorthin, liest den guenstigsten Preis der Hinstrecke, wechselt zur
Ruecksstrecke und summiert beide - zwei echte Einzelstrecken-Bestpreise,
keine garantiert buchbare Kombination (bleibt Recherche-Referenz).
"""
from __future__ import annotations

import base64
import contextlib
import json
import re
from datetime import date
from urllib.parse import urlencode

from ..config import Config
from ..logging_setup import get_logger
from .scraper_base import browser_page, goto, save_screenshot

log = get_logger("source.ita_matrix")
SOURCE = "ita_matrix"

BASE = "https://matrix.itasoftware.com/flights"
_PRICE = re.compile(r"(\d{1,3}(?:[.,]\d{3})*)\s?€")


def _payload(origin: str, dest: str, out_d: date, ret_d: date, adults: int) -> dict:
    return {
        "type": "round-trip",
        "slices": [{
            "origin": [origin], "dest": [dest],
            "dates": {
                "searchDateType": "specific",
                "departureDate": out_d.isoformat(), "departureDateType": "depart",
                "departureDateModifier": "0", "departureDatePreferredTimes": [],
                "returnDate": ret_d.isoformat(), "returnDateType": "depart",
                "returnDateModifier": "0", "returnDatePreferredTimes": [],
            },
        }],
        "options": {"cabin": "COACH", "stops": "-1", "extraStops": "1",
                    "allowAirportChanges": "true", "showOnlyAvailable": "true"},
        "pax": {"adults": str(adults)},
    }


def _url(origin: str, dest: str, out_d: date, ret_d: date, adults: int) -> str:
    b64 = base64.b64encode(json.dumps(_payload(origin, dest, out_d, ret_d, adults)).encode()).decode()
    return f"{BASE}?search={b64}"


def _google_flights_crosscheck_url(origin: str, dest: str, out_d: date, ret_d: date) -> str:
    """Bewusst simpler Link (keine tfs-Protobuf-Kodierung noetig): Google
    Flights akzeptiert eine einfache Textsuche mit Datum, damit der Nutzer
    denselben Zeitraum direkt buchbar nachschauen kann - die eigentliche
    "wo buche ich das" Antwort, da man auf ITA Matrix selbst nicht bucht."""
    q = f"Fluege von {origin} nach {dest} am {out_d.isoformat()} zurueck {ret_d.isoformat()}"
    return "https://www.google.com/travel/flights?" + urlencode({"q": q, "curr": "EUR", "hl": "de"})


def _cheapest_from_body(body: str) -> float | None:
    prices = [float(m.replace(".", "").replace(",", ".")) for m in _PRICE.findall(body)]
    # Nur plausible Flugpreise (keine Jahreszahlen/IDs, die zufaellig wie
    # "1.234 €" aussehen wuerden - in der Praxis nicht relevant, da '€'
    # nur bei echten Preiszellen im Ergebnistext vorkommt).
    prices = [p for p in prices if 30 <= p <= 50000]
    return min(prices) if prices else None


async def _search_one(cfg: Config, url: str) -> tuple[float | None, str]:
    """Bugfix 14.09.26 (Nutzer-Fund: "hab Ergebnisse bekommen, du bist zu
    dumm" - zurecht): der "Complete Trips"-Tab (Standardansicht nach dem
    Laden) ist fuer FRA-USM leer, ITA Matrix berechnet dort offenbar keine
    kompletten Routings. Die "Individual Flights"-Matrix (Preis je Airline x
    Stopp-Zahl, getrennt pro Strecke) hat dagegen echte Daten. Deshalb: auf
    "Individual Flights" klicken, guenstigsten Preis der Hinstrecke lesen,
    dann auf die Ruecksstrecke ("2 ...") wechseln und deren guenstigsten
    Preis addieren - Summe zweier Einzelstrecken-Bestpreise, keine echte
    Buchung (bleibt eine Recherche-Referenz, siehe Modul-Docstring)."""
    async with browser_page(cfg) as page:
        await goto(page, url)
        with contextlib.suppress(Exception):
            await page.wait_for_function(
                "document.body.innerText.includes('Individual Flights')", timeout=30000)
        with contextlib.suppress(Exception):
            # force=True: ohne das registriert der Klick in Playwright hier
            # manchmal stillschweigend NICHT (kein Fehler, aber der Tab
            # wechselt trotzdem nicht - live verifiziert 14.09.26).
            await page.get_by_text("Individual Flights", exact=False).first.click(
                timeout=5000, force=True)
        with contextlib.suppress(Exception):
            await page.wait_for_function(
                "document.body.innerText.includes('€')", timeout=60000)
        with contextlib.suppress(Exception):
            await page.wait_for_timeout(1500)
        body = ""
        with contextlib.suppress(Exception):
            body = await page.inner_text("body", timeout=6000)
        out_price = _cheapest_from_body(body)

        ret_price = None
        with contextlib.suppress(Exception):
            # Zweite Strecke ("2 <Ziel> to <Start>") anklicken - eigene
            # Matrix, eigener guenstigster Preis. KEIN wait_for_function auf
            # '€' hier - die Hinstrecken-Preise stehen zu diesem Zeitpunkt
            # noch im DOM/Text, das wuerde sofort (fälschlich) erfuellt sein,
            # bevor die neue Matrix ueberhaupt geladen ist. Stattdessen fest
            # auf die beobachtete Ladezeit warten (live verifiziert 14.09.26:
            # bis zu 45s bis die Matrix nach einem Tab-Wechsel rendert).
            leg2 = page.get_by_text(re.compile(r"^2\b"), exact=False).first
            if await leg2.count():
                await leg2.click(timeout=5000, force=True)
                await page.wait_for_timeout(48000)
                body2 = await page.inner_text("body", timeout=6000)
                ret_price = _cheapest_from_body(body2)

        if out_price is None:
            if cfg.sources.scraper.screenshot_on_error:
                await save_screenshot(page, SOURCE)
            return None, body[:400]
        total = out_price + ret_price if ret_price is not None else out_price * 2
        return total, ""


async def fetch(cfg: Config) -> list[dict]:
    """Sucht fuer jeden Heimatflughafen (primaeres Datumspaar) 1x mit 1 Pax,
    rechnet x Personenzahl hoch - gleiche Methodik wie sources/flights.py."""
    t = cfg.trip
    ic = cfg.sources.ita_matrix
    if not ic.enabled:
        return []
    pairs = t.rt_date_pairs()
    if not pairs:
        return []
    out_d, ret_d = pairs[0]
    origins = ic.origins or t.origin_airports
    dest = t.destination_airport
    results: list[dict] = []
    import asyncio
    import random
    lo, hi = ic.request_delay_seconds
    for i, origin in enumerate(origins):
        url_1pax = _url(origin, dest, out_d, ret_d, 1)
        url_group = _url(origin, dest, out_d, ret_d, t.persons)
        gflights = _google_flights_crosscheck_url(origin, dest, out_d, ret_d)
        row = {
            "origin": origin, "destination": dest,
            "search_date": out_d.isoformat(), "return_date": ret_d.isoformat(),
            "deep_link_1pax": url_1pax, "deep_link_group": url_group,
            "google_flights_link": gflights,
            "ok": False, "price_per_person": None, "price_total": None, "error": "",
        }
        try:
            price_pp, snippet = await _search_one(cfg, url_1pax)
            if price_pp is None:
                row["error"] = "kein Preis lesbar (evtl. Route nicht bedient oder Timeout)"
                log.warning("%s %s->%s: kein Preis (%s)", SOURCE, origin, dest, snippet[:120])
            else:
                row["ok"] = True
                row["price_per_person"] = round(price_pp, 2)
                row["price_total"] = round(price_pp * t.persons, 2)
                log.info("%s %s->%s: %.0f EUR p.P. (x%d = %.0f EUR)",
                        SOURCE, origin, dest, price_pp, t.persons, row["price_total"])
        except Exception as exc:  # noqa: BLE001
            row["error"] = f"{type(exc).__name__}: {exc}"
            log.warning("%s %s->%s: Fehler %s", SOURCE, origin, dest, exc)
        results.append(row)
        if i < len(origins) - 1:
            await asyncio.sleep(random.uniform(lo, hi))
    return results


def collect(cfg: Config) -> tuple[list[dict], dict]:
    """Synchroner Wrapper (gleiches Muster wie sources/hotels/__init__.py)."""
    from .scraper_base import playwright_available

    if not cfg.sources.ita_matrix.enabled:
        return [], {"ok": True, "count": 0, "attempted": 0, "error": "deaktiviert"}
    ok, err = playwright_available()
    if not ok:
        return [], {"ok": False, "count": 0, "attempted": 0, "error": err}
    import asyncio

    try:
        rows = asyncio.run(fetch(cfg))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            rows = loop.run_until_complete(fetch(cfg))
        finally:
            loop.close()
    except Exception as exc:  # noqa: BLE001
        return [], {"ok": False, "count": 0, "attempted": 0,
                    "error": f"{type(exc).__name__}: {exc}"}
    good = sum(1 for r in rows if r["ok"])
    return rows, {
        "ok": good > 0, "count": good, "attempted": len(rows),
        "error": "; ".join(f"{r['origin']}: {r['error']}" for r in rows
                           if not r["ok"] and r["error"]) or "",
    }
