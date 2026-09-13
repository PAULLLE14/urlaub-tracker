"""Angebote normalisieren & deduplizieren.

Gleicher Flug (gleiche Route, gleiche erste Abflugzeit, gleiche Airlines) aus
mehreren Quellen -> ein Eintrag mit dem guenstigsten Preis/Link. Quellenliste
wird in ``source`` zusammengefuehrt (``a+b``).
"""
from __future__ import annotations

from ..logging_setup import get_logger
from ..offers import FlightOffer

log = get_logger("logic.normalize")


def dedup_flights(offers: list[FlightOffer]) -> list[FlightOffer]:
    best: dict[tuple, FlightOffer] = {}
    for off in offers:
        try:
            key = off.dedup_key
        except Exception:
            key = (off.direction, off.origin, off.destination,
                   str(off.search_date), off.price_total)
        cur = best.get(key)
        if cur is None:
            best[key] = off
            continue
        # Ein ausgeschlossenes Angebot darf NIE ein gueltiges verdraengen,
        # egal wie viel guenstiger sein (widerlegter/ungueltiger) Preis
        # aussieht - sonst koennte z.B. eine per Gruppen-Check widerlegte
        # 1-Pax-Hochrechnung eine teurere, aber tatsaechlich bestaetigte
        # Buchung verstecken (Bugfix 11.09.26). Nur unter gleichstehenden
        # (beide excluded oder beide gueltig) entscheidet der Preis.
        if cur.excluded != off.excluded:
            winner, loser = (cur, off) if off.excluded else (off, cur)
        elif off.price_total < cur.price_total:
            winner, loser = off, cur
        else:
            winner, loser = cur, off
        srcs = sorted({*winner.source.split("+"), *loser.source.split("+")})
        winner.source = "+".join(srcs)
        best[key] = winner

    result = list(best.values())
    if len(result) != len(offers):
        log.info("Dedup: %d -> %d Flugangebote", len(offers), len(result))
    return result
