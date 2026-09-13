"""Pauschalreise-Quellen buendeln.

Primaer: CHECK24 (live, Zimmergroessen-Split wie beim Hotelvergleich).
Zusaetzlich: generische Portal-Scraper (TUI, DERTOUR, alltours, REWE Reisen,
sonnenklar.TV) - Selektor-basiert, siehe specs.py.
"""
from __future__ import annotations

import asyncio

from ...config import Config
from ...logging_setup import get_logger
from ...offers import PackageOffer
from ..scraper_base import playwright_available
from . import check24
from .portal_base import run_portal_search
from .specs import ALL_SPECS

log = get_logger("source.packages")


async def _collect_async(cfg: Config, names: list[str], use_check24: bool) -> list[PackageOffer]:
    tasks = [run_portal_search(cfg, ALL_SPECS[n]) for n in names]
    labels = list(names)
    if use_check24:
        tasks.append(check24.fetch(cfg))
        labels.append("check24")

    results = await asyncio.gather(*tasks, return_exceptions=True)
    offers: list[PackageOffer] = []
    for name, res in zip(labels, results):
        if isinstance(res, Exception):
            offers.append(PackageOffer(source=name, ok=False, price_total=None,
                                       currency=cfg.trip.currency,
                                       error=f"{type(res).__name__}: {res}"))
        else:
            offers.extend(res)
    return offers


def collect_packages(cfg: Config) -> tuple[list[PackageOffer], dict]:
    ok, err = playwright_available()
    if not ok:
        return [], {"ok": False, "count": 0,
                    "error": f"playwright nicht verfuegbar: {err}"}

    names = [n for n in cfg.sources.packages.enabled_names() if n in ALL_SPECS]
    use_check24 = cfg.sources.packages.check24.enabled
    if not names and not use_check24:
        return [], {"ok": True, "count": 0, "error": "alle Quellen deaktiviert"}

    try:
        offers = asyncio.run(_collect_async(cfg, names, use_check24))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            offers = loop.run_until_complete(_collect_async(cfg, names, use_check24))
        finally:
            loop.close()

    good = sum(1 for o in offers if o.ok)
    return offers, {
        "ok": good > 0,
        "count": good,
        "attempted": len(names) + (1 if use_check24 else 0),
        "error": "; ".join(sorted({f"{o.source}: {o.error}" for o in offers if not o.ok}))
        or "",
    }
