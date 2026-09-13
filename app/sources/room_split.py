"""Geteilt genutzte Logik: Personen auf Zimmer verteilen.

Fuer Quellen, die (wie CHECK24) "3 Zimmer/8 Personen" in einer Suche
schlecht/inkonsistent bepreisen. Zuverlaessiger: jede Zimmergroesse EINZELN
suchen (1 Zimmer, N Erwachsene) und die guenstigsten Treffer je Groesse x
Anzahl Zimmer dieser Groesse aufsummieren.
"""
from __future__ import annotations


def room_split(persons: int, rooms: int, max_per_room: int) -> dict[int, int]:
    """z.B. 8 Personen / 3 Zimmer / max 3 -> {3: 2, 2: 1} (Groesse -> Anzahl)."""
    if rooms <= 0:
        return {}
    base, rem = divmod(persons, rooms)
    sizes = [base + (1 if i < rem else 0) for i in range(rooms)]
    counts: dict[int, int] = {}
    for s in sizes:
        if s <= 0:
            continue
        counts[s] = counts.get(s, 0) + 1
    return counts


def partitions(total: int, max_part: int, min_part: int = 1) -> list[list[int]]:
    """Alle Partitionen von `total` in Teile zwischen min_part und
    max_part (absteigend sortiert, keine Permutationen doppelt). Oeffentlich
    (nicht nur fuer Zimmer) - z.B. auch fuer Split-Ticket-Personenzahlen
    genutzt (siehe flights_group_browser.py)."""
    results: list[list[int]] = []

    def rec(remaining: int, max_allowed: int, current: list[int]) -> None:
        if remaining == 0:
            results.append(list(current))
            return
        top = min(max_allowed, remaining, max_part)
        for p in range(top, min_part - 1, -1):
            current.append(p)
            rec(remaining - p, p, current)
            current.pop()

    rec(total, max_part, [])
    return results


def _to_counts(sizes: list[int]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for s in sizes:
        counts[s] = counts.get(s, 0) + 1
    return counts


def candidate_allocations(persons: int, max_per_room: int,
                          min_per_room: int = 1) -> list[dict[int, int]]:
    """Alle sinnvollen Zimmer-Groessen-Verteilungen fuer `persons` Personen
    (13.09.26 Nutzervorgabe: 2x3+1x2 ist nicht automatisch am guenstigsten -
    alle Aufteilungen pruefen). Enthaelt:
      - alle exakten Partitionen (Zimmergroessen summieren exakt zu `persons`,
        z.B. {3:2, 2:1} oder {2:4})
      - die "auf Maximalgroesse aufrunden"-Variante (alle Zimmer auf
        max_per_room, so viele wie noetig - z.B. {3:3} = 9 Kapazitaet fuer
        8 Personen, falls ein Zimmer WENIGER + ein leerer Platz guenstiger
        ist als ein Zimmer mehr mit weniger Personen)
    Jede Allokation ist ein {Zimmergroesse: Anzahl}-Dict. Gibt eine LEERE
    Liste zurueck, wenn keine gueltige Aufteilung existiert (z.B.
    max_per_room <= 0)."""
    if persons <= 0 or max_per_room <= 0:
        return []
    out = [_to_counts(p) for p in partitions(persons, max_per_room, min_per_room)]
    rooms_needed = -(-persons // max_per_room)  # ceil
    rounded = {max_per_room: rooms_needed}
    if rounded not in out:
        out.append(rounded)
    return out


def cheapest_allocation(allocations: list[dict[int, int]],
                        price_per_size: dict[int, float]) -> tuple[dict[int, int], float] | None:
    """Waehlt aus `allocations` die guenstigste, fuer die ALLE benoetigten
    Zimmergroessen einen Preis in `price_per_size` haben. Gibt
    (Allokation, Gesamtpreis) zurueck, None wenn keine vollstaendig
    bepreisbar ist."""
    best: tuple[dict[int, int], float] | None = None
    for alloc in allocations:
        if not all(size in price_per_size for size in alloc):
            continue
        total = sum(price_per_size[size] * count for size, count in alloc.items())
        if best is None or total < best[1]:
            best = (alloc, total)
    return best
