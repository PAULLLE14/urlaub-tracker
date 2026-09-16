"""Gemeinsame Playwright-Helfer fuer alle Scraper (Hotel + Pauschalportale).

Design-Grundsatz: ein Scraper darf NIE den Gesamtlauf abschiessen. Alles ist
in try/except gekapselt, Fehler werden geloggt und (optional) als Screenshot
nach ``artifacts/`` geschrieben, damit man sieht, was die Seite gerade zeigt.
Layout-Aenderungen der Portale => Selektor hier/pro Adapter nachziehen.
"""
from __future__ import annotations

import contextlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from ..config import Config
from ..logging_setup import get_logger

log = get_logger("source.scraper")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
ARTIFACT_DIR = BASE_DIR / "artifacts"

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

try:
    from playwright.async_api import async_playwright

    _HAVE_PW = True
except Exception as exc:  # pragma: no cover
    _HAVE_PW = False
    _PW_IMPORT_ERROR = repr(exc)


def playwright_available() -> tuple[bool, str]:
    return _HAVE_PW, ("" if _HAVE_PW else _PW_IMPORT_ERROR)


@contextlib.asynccontextmanager
async def browser_page(cfg: Config) -> AsyncIterator:
    """Liefert eine frische Playwright-Page (Chromium) mit sinnvollen Defaults."""
    sc = cfg.sources.scraper
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=sc.headless,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled",
                  "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            locale=sc.locale,
            user_agent=_UA,
            viewport={"width": 1440, "height": 900},
            timezone_id="Europe/Berlin",
            extra_http_headers={"Accept-Language": f"{sc.locale},de;q=0.9,en;q=0.8"},
        )
        context.set_default_navigation_timeout(sc.nav_timeout_seconds * 1000)
        context.set_default_timeout(sc.nav_timeout_seconds * 1000)
        # leichter Stealth-Touch
        await context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        page = await context.new_page()
        try:
            yield page
        finally:
            with contextlib.suppress(Exception):
                await context.close()
            with contextlib.suppress(Exception):
                await browser.close()


async def goto(page, url: str) -> None:
    await page.goto(url, wait_until="domcontentloaded")
    with contextlib.suppress(Exception):
        await page.wait_for_load_state("networkidle", timeout=8000)


async def dismiss_consent(page) -> None:
    """Cookie-/Consent-Banner wegklicken (nur ablehnen/notwendig)."""
    selectors = [
        "#onetrust-reject-all-handler",
        "button#onetrust-accept-btn-handler",
        "[data-testid='cookie-banner-decline']",
        "button[aria-label*='ablehnen' i]",
        "button:has-text('Nur notwendige')",
        "a:has-text('Nur notwendige')",
        "text=Nur notwendige Cookies",
        "button:has-text('Alle ablehnen')",
        "button:has-text('Ablehnen')",
        "button:has-text('Accept')",
        "#cmpwelcomebtnno a",
    ]
    for sel in selectors:
        with contextlib.suppress(Exception):
            el = page.locator(sel).first
            if await el.is_visible(timeout=1200):
                await el.click(timeout=1500)
                log.debug("Consent via %s geschlossen", sel)
                await page.wait_for_timeout(400)
                return


async def wait_for_stable_result_count(page, marker_text: str, *, stable_for: float = 2.0,
                                       max_wait: float = 15.0, poll_interval: float = 0.5) -> int:
    """Wartet, bis die Anzahl der Vorkommen von ``marker_text`` im
    sichtbaren Seitentext ueber ``stable_for`` Sekunden nicht mehr
    zunimmt (statt einer festen Wartezeit, die bei Google Flights nicht
    zuverlaessig reicht - 13.09.26 Nutzervorgabe). Bricht spaetestens nach
    ``max_wait`` Sekunden ab. Gibt die zuletzt gesehene Anzahl zurueck."""
    js = "(m) => (document.body.innerText.match(new RegExp(m, 'g')) || []).length"
    elapsed = 0.0
    last_count = -1
    stable_since = 0.0
    while elapsed < max_wait:
        with contextlib.suppress(Exception):
            count = await page.evaluate(js, marker_text)
            if count == last_count and count > 0:
                stable_since += poll_interval
                if stable_since >= stable_for:
                    return count
            else:
                stable_since = 0.0
            last_count = count
        await page.wait_for_timeout(int(poll_interval * 1000))
        elapsed += poll_interval
    return max(last_count, 0)


async def expand_more_results(page) -> None:
    """Klickt "Mehr Flüge ansehen" / "Weitere Flüge anzeigen" (Google
    Flights zeigt zunaechst nur eine Kurzliste) - best effort, mehrfach
    versuchen, falls nach dem Klick noch ein weiterer "mehr"-Button
    nachlaedt."""
    selectors = [
        "text=Mehr Flüge ansehen", "text=Weitere Flüge anzeigen",
        "button:has-text('Mehr Flüge')", "button:has-text('Weitere Flüge')",
    ]
    for _ in range(3):
        clicked = False
        for sel in selectors:
            with contextlib.suppress(Exception):
                el = page.locator(sel).first
                if await el.is_visible(timeout=800):
                    await el.click(timeout=1500, force=True)
                    clicked = True
                    await page.wait_for_timeout(800)
                    break
        if not clicked:
            return


async def select_cheapest_tab(page) -> bool:
    """Klickt den "Am günstigsten"-Tab bei Google Flights statt des
    default-aktiven "Beste Flüge"-Tabs (sortiert nach Preis+Komfort-Mix,
    NICHT nach dem tatsaechlich guenstigsten Preis). Nutzer-Fund 16.09.26:
    Dashboard zeigte 7.944 EUR als guenstigsten Preis - auf derselben
    Google-Seite, nur auf diesem separaten Tab, stand der echte guenstigste
    Preis von 7.352 EUR. Ohne diesen Klick liest der Parser bestenfalls einen
    von Google "empfohlenen", aber nicht den billigsten Flug. force=True
    noetig (wie beim ITA-Matrix-Tab-Klick) - ein einfacher Klick registriert
    hier oft nicht. Gibt True zurueck, wenn der Tab gefunden/geklickt wurde -
    False ist kein Fehler (manche Suchen zeigen nur einen Tab, wenn "Beste
    Fluege" und "guenstigste" identisch sind)."""
    with contextlib.suppress(Exception):
        tab = page.get_by_role("tab", name=re.compile("Am günstigsten"))
        if await tab.count():
            await tab.first.click(timeout=5000, force=True)
            await page.wait_for_timeout(600)
            return True
    return False


async def save_screenshot(page, source: str) -> str:
    ARTIFACT_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = ARTIFACT_DIR / f"{source}_{ts}.png"
    with contextlib.suppress(Exception):
        await page.screenshot(path=str(path), full_page=True)
        return str(path.relative_to(BASE_DIR))
    return ""


# --------------------------------------------------------------------------- #
# Preis-Parsing (DE + EN Formate)
# --------------------------------------------------------------------------- #
_CUR_HINT = re.compile(r"(EUR|€|CHF|USD|\$)", re.I)
_NUM = re.compile(r"\d[\d.\s ,]*\d|\d")


def parse_money(text: str | None) -> float | None:
    """'1.234,56 €' / '€ 1,234.56' / 'ab 12.345 EUR' -> float. None wenn nichts."""
    if not text:
        return None
    m = _NUM.search(text.replace(" ", " "))
    if not m:
        return None
    raw = m.group(0).strip().replace(" ", "").replace(" ", "")
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):     # 1.234,56  (DE)
            raw = raw.replace(".", "").replace(",", ".")
        else:                                    # 1,234.56  (EN)
            raw = raw.replace(",", "")
    elif "," in raw:
        # 1,234 -> Tausender? oder 12,50 -> Dezimal?
        if len(raw.split(",")[-1]) == 2:
            raw = raw.replace(",", ".")
        else:
            raw = raw.replace(",", "")
    else:
        # nur Punkte: 1.234 (Tausender DE) vs 1234.50
        if raw.count(".") == 1 and len(raw.split(".")[-1]) == 3:
            raw = raw.replace(".", "")
    try:
        val = float(raw)
        return val if val > 0 else None
    except ValueError:
        return None


async def first_text(page, selectors: list[str]) -> tuple[str, str]:
    """Erster Selektor, der sichtbaren Text liefert -> (text, selektor)."""
    for sel in selectors:
        with contextlib.suppress(Exception):
            loc = page.locator(sel).first
            if await loc.count() and await loc.is_visible(timeout=1500):
                txt = (await loc.inner_text(timeout=1500)).strip()
                if txt:
                    return txt, sel
    return "", ""


async def all_prices_on_page(page, selectors: list[str], limit: int = 40) -> list[float]:
    found: list[float] = []
    for sel in selectors:
        with contextlib.suppress(Exception):
            loc = page.locator(sel)
            n = min(await loc.count(), limit)
            for i in range(n):
                with contextlib.suppress(Exception):
                    t = await loc.nth(i).inner_text(timeout=800)
                    if _CUR_HINT.search(t or ""):
                        v = parse_money(t)
                        if v:
                            found.append(v)
    return found
