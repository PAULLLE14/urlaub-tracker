"""Quellenuebergreifender Mix: je Zimmergroesse der guenstigste Anbieter ueber ALLE Quellen.

Nutzer 07.11.26: 2er- und 3er-Zimmer duerfen bei verschiedenen Anbietern gebucht
werden. Bisher kombinierte nur Google Hotels intern. 21.11.26 (Audit, Lauf #302):
CHECK24 hatte das guenstigste 3er-Zimmer (3.046 EUR), Google das guenstigste 2er
(2.547 EUR) - zusammen 8.639 EUR statt 8.775 EUR (CHECK24 allein), und das fiel
niemandem auf. Hier wird deshalb ueber alle Quellen kombiniert.
"""
from __future__ import annotations

from datetime import date

from ...config import Config
from ...offers import HotelOffer
from ..room_split import candidate_allocations, cheapest_allocation, explicit_allocations
from .santiburi import TAX_FEE_MULTIPLIER

_DIRECT = "santiburi_official"


def _comparable(o: HotelOffer) -> float:
    return (o.raw or {}).get("price_total_incl_fees_estimate") or o.price_total or 0.0


def cross_mix(cfg: Config, offers: list[HotelOffer], checkin: date, checkout: date) -> HotelOffer | None:
    """Guenstigster Mix ueber alle Quellen; None, wenn er nicht billiger als jeder
    Einzelanbieter ist."""
    t = cfg.trip
    nights = (checkout - checkin).days
    allocations = (explicit_allocations(t.hotel.allowed_room_shapes, t.persons)
                   if t.hotel.allowed_room_shapes
                   else candidate_allocations(t.persons, t.max_persons_per_room))
    sizes = sorted({s for a in allocations for s in a})
    ci, co = checkin.isoformat(), checkout.isoformat()

    best: dict[int, dict] = {}     # Zimmergroesse -> guenstigster Kandidat
    singles: list[float] = []
    for o in offers:
        if not o.ok or o.is_reference or not o.price_total or o.source.startswith("Mix:"):
            continue
        raw = o.raw or {}
        if raw.get("checkin") and (raw["checkin"], raw.get("checkout")) != (ci, co):
            continue
        singles.append(_comparable(o))
        for key, info in (raw.get("per_room_size") or {}).items():
            price = info.get("cheapest")
            if not price:
                continue
            if o.source == _DIRECT:
                price, label = price * TAX_FEE_MULTIPLIER, "Santiburi direkt"
            elif o.source == "check24":
                label = f"CHECK24 ({info.get('supplier') or 'Anbieter'})"
            else:
                label = info.get("provider") or raw.get("provider") or o.source.replace(" (Google)", "")
            size = int(key)
            if size in sizes and (size not in best or price < best[size]["price"]):
                best[size] = {"price": float(price), "label": label, "url": info.get("url", ""),
                              "per_night": round(price / nights, 2) if nights else None}

    choice = cheapest_allocation(allocations, {s: c["price"] for s, c in best.items()})
    if not choice:
        return None
    counts, total = choice
    if singles and total >= min(singles) - 0.5:
        return None
    parts = " + ".join(f"{best[s]['label']} ({n}×{s}er)" for s, n in sorted(counts.items(), reverse=True))
    info = {str(s): {"url": best[s]["url"], "cheapest": round(best[s]["price"], 2),
                     "per_night": best[s]["per_night"], "provider": best[s]["label"]}
            for s in counts}
    return HotelOffer(
        source=f"Mix: {parts}", ok=True, price_total=round(total, 2), currency=t.currency,
        nights=nights, rooms=sum(counts.values()), guests=t.persons,
        per_night=round(total / nights, 2) if nights else None,
        deep_link=next(iter(info.values()))["url"], room_desc=t.hotel.room_type_hint,
        raw={"provider": "Mix", "via": "Zimmer bei verschiedenen Anbietern, ueber alle Quellen",
             "checkin": ci, "checkout": co,
             "room_split": {str(k): v for k, v in counts.items()},
             "per_room_size": info, "all_in": True,
             "basis": "Je Zimmergroesse der guenstigste Anbieter ueber alle Quellen "
                      "(Google Hotels, CHECK24, Santiburi direkt), getrennt gebucht"})
