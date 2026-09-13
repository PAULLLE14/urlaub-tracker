"""Flughafen-Geodaten & Distanzhelfer.

Nutzt das Offline-Paket ``airportsdata`` (IATA -> lat/lon/tz/name).  Fuer ein
paar reiserelevante Codes gibt es einen hartkodierten Fallback, falls das
Paket fehlt oder einen Code nicht kennt.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

try:
    import airportsdata

    _DB = airportsdata.load("IATA")
except Exception:  # pragma: no cover - Paket optional
    _DB = {}

# Minimaler Fallback (lat, lon, tz, name)
_FALLBACK = {
    "STR": (48.6899, 9.2219, "Europe/Berlin", "Stuttgart"),
    "MUC": (48.3538, 11.7861, "Europe/Berlin", "Muenchen Franz Josef Strauss"),
    "FRA": (50.0379, 8.5622, "Europe/Berlin", "Frankfurt am Main"),
    "ZRH": (47.4647, 8.5492, "Europe/Zurich", "Zuerich"),
    "USM": (9.5479, 100.0623, "Asia/Bangkok", "Koh Samui"),
    "BKK": (13.6900, 100.7501, "Asia/Bangkok", "Bangkok Suvarnabhumi"),
    "DMK": (13.9126, 100.6068, "Asia/Bangkok", "Bangkok Don Mueang"),
    "DOH": (25.2731, 51.6081, "Asia/Qatar", "Doha Hamad"),
    "DXB": (25.2532, 55.3657, "Asia/Dubai", "Dubai"),
    "AUH": (24.4330, 54.6511, "Asia/Dubai", "Abu Dhabi"),
    "IST": (41.2753, 28.7519, "Europe/Istanbul", "Istanbul"),
    "SIN": (1.3644, 103.9915, "Asia/Singapore", "Singapore Changi"),
    "HKG": (22.3080, 113.9185, "Asia/Hong_Kong", "Hong Kong"),
    "DEL": (28.5562, 77.1000, "Asia/Kolkata", "Delhi"),
    "CMB": (7.1808, 79.8841, "Asia/Colombo", "Colombo"),
    "KUL": (2.7456, 101.7099, "Asia/Kuala_Lumpur", "Kuala Lumpur"),
    "AMS": (52.3105, 4.7683, "Europe/Amsterdam", "Amsterdam Schiphol"),
    "CDG": (49.0097, 2.5479, "Europe/Paris", "Paris Charles de Gaulle"),
    "LHR": (51.4700, -0.4543, "Europe/London", "London Heathrow"),
    "MMR": (0, 0, "UTC", "unknown"),
}

_EU_TZ_PREFIXES = ("Europe/",)


@dataclass(frozen=True)
class Airport:
    code: str
    lat: float
    lon: float
    tz: str
    name: str

    @property
    def known(self) -> bool:
        return self.name != "unknown"


@lru_cache(maxsize=512)
def get_airport(code: str) -> Airport:
    code = (code or "").strip().upper()
    rec = _DB.get(code)
    if rec and rec.get("lat") is not None:
        return Airport(code, float(rec["lat"]), float(rec["lon"]),
                       rec.get("tz") or "UTC", rec.get("name") or code)
    fb = _FALLBACK.get(code)
    if fb:
        return Airport(code, fb[0], fb[1], fb[2], fb[3])
    return Airport(code, 0.0, 0.0, "UTC", "unknown")


def haversine_km(a: Airport, b: Airport) -> float:
    if not (a.known or (a.lat or a.lon)) or not (b.known or (b.lat or b.lon)):
        return 0.0
    r = 6371.0
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dphi = math.radians(b.lat - a.lat)
    dlmb = math.radians(b.lon - a.lon)
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def route_distance_km(codes: list[str]) -> float:
    """Summe der Grosskreis-Distanzen entlang einer Flughafen-Kette."""
    aps = [get_airport(c) for c in codes]
    return sum(haversine_km(aps[i], aps[i + 1]) for i in range(len(aps) - 1))


def is_in_europe(code: str) -> bool:
    return get_airport(code).tz.startswith(_EU_TZ_PREFIXES)
