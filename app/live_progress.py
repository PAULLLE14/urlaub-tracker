"""In-Memory-Fortschrittsanzeige fuer einen laufenden CheckRun.

Nutzer-Fund 16.09.26: "muss das wirklich 45-50 min dauern bis irgendwas
kommt? koennen die ergebnisse nach und nach kommen?" - der Gruppen-Check
(flights_group_browser.py) ist der grosse Zeitfresser (25+ echte Browser-
Suchen nacheinander), macht aber bis dahin praktisch die gesamte Laufzeit
aus, WAEHREND der die DB erst am Ende des kompletten Laufs geschrieben wird
(siehe collector.py Schritt 5 "Persistenz").

Bewusst NICHT in die eigentliche Persistenz (FlightOfferRow/BestSnapshot)
eingebaut: die persistierten Daten muessen konsistent/vollstaendig fuer
Trend/Verlauf sein (unvollstaendige Zwischenstaende dort wuerden Chart/
Ampel verfaelschen). Dieser Zustand hier ist rein ephemer, pro Prozess (ein
einziger Uvicorn-Worker, siehe Dockerfile), geht bei einem Neustart verloren
und ist NUR eine Fortschritts-/Live-Anzeige - kein Ersatz fuer die echten
CheckRun-Daten.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

_lock = threading.Lock()
_state: dict = {
    "run_id": None,
    "phase": "",
    "checked": 0,
    "total": 0,
    "best_price": None,
    "best_label": "",
    "recent": [],          # Liste der letzten Funde, neueste zuerst
    "updated_at": None,
    "running": False,
}
_RECENT_MAX = 8


def start(run_id: int) -> None:
    with _lock:
        _state.update(run_id=run_id, phase="Fluege werden gesammelt", checked=0, total=0,
                      best_price=None, best_label="", recent=[],
                      updated_at=datetime.now(timezone.utc).isoformat(), running=True)


def set_phase(phase: str, total: int = 0) -> None:
    with _lock:
        _state["phase"] = phase
        _state["total"] = total
        _state["checked"] = 0
        _state["updated_at"] = datetime.now(timezone.utc).isoformat()


def report(*, label: str, price: float | None, confirmed: bool) -> None:
    """Wird nach jeder einzelnen Routen-/Optionen-Pruefung aufgerufen (auch
    wenn kein Preis lesbar war - dann price=None, zaehlt aber als 'geprueft').
    ``confirmed=False`` fuer Angebote, die als parse_suspect o.ae. NICHT als
    guenstigster Preis in Frage kommen."""
    with _lock:
        _state["checked"] += 1
        _state["updated_at"] = datetime.now(timezone.utc).isoformat()
        if price is not None:
            entry = {"label": label, "price": price, "confirmed": confirmed}
            _state["recent"].insert(0, entry)
            _state["recent"] = _state["recent"][:_RECENT_MAX]
            if confirmed and (_state["best_price"] is None or price < _state["best_price"]):
                _state["best_price"] = price
                _state["best_label"] = label


def finish() -> None:
    with _lock:
        _state["running"] = False
        _state["phase"] = "fertig"
        _state["updated_at"] = datetime.now(timezone.utc).isoformat()


def snapshot() -> dict:
    with _lock:
        return dict(_state, recent=list(_state["recent"]))
