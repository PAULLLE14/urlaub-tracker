"""Agoda-Preisgarantie-Waechter.

Der Hotel-Aufenthalt ist bei Agoda gebucht (23.11.26, 8.977,68 EUR). Die Preisgarantie
(agoda.com/info/agoda-policies.html) gleicht nur Preise von bestimmten "Other Platforms"
aus (Booking.com, Trip.com, Expedia, Hotels.com, Traveloka ...) - Super.com, Stayforlong,
CHECK24, Google & Co. zaehlen nicht. Ist ``alerts.guarantee_platforms`` gesetzt, wertet der
Tracker deshalb nur noch diese Plattformen aus (Einzelanbieter, keine Kombinationen).
"""
from __future__ import annotations

from .config import Config


def active(cfg: Config) -> bool:
    return bool(cfg.alerts.guarantee_platforms and cfg.alerts.booked_hotel_price)


def is_eligible(source: str, platforms: list[str]) -> bool:
    """Gehoert dieses Angebot (``"Trip.com (Google)"``) zu einer anerkannten Plattform?
    Kombinationen aus mehreren Anbietern sind nicht dieselbe Buchung und zaehlen nicht."""
    if source.startswith("Mix:"):
        return False
    low = source.lower()
    return any(p.lower() in low for p in platforms)
