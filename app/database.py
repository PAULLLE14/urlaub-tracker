"""DB-Engine & Session.

Standard: lokale SQLite-Datei ``data/prices.db``.  Setzt man ``DATABASE_URL``
(z.B. ``postgresql+psycopg://user:pw@host/db``), wird stattdessen diese
benutzt - identischer Code, nur anderer Treiber.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_secrets

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"


class Base(DeclarativeBase):
    pass


def _url() -> str:
    sec = get_secrets()
    if sec.database_url:
        return sec.database_url
    DATA_DIR.mkdir(exist_ok=True)
    return f"sqlite:///{(DATA_DIR / 'prices.db').as_posix()}"


_engine = None
_Session: sessionmaker | None = None


def get_engine():
    global _engine, _Session
    if _engine is None:
        url = _url()
        kw: dict = {"pool_pre_ping": True, "future": True}
        if url.startswith("sqlite"):
            kw["connect_args"] = {"check_same_thread": False}
        _engine = create_engine(url, **kw)
        _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_session():
    if _Session is None:
        get_engine()
    assert _Session is not None
    return _Session()


def init_db() -> None:
    from . import models  # noqa: F401  (Modelle registrieren)

    eng = get_engine()
    Base.metadata.create_all(eng)
    _migrate(eng)


# Leichtgewichtige "Migration": fehlende Spalten nachruesten, damit eine
# bestehende DB nach einem Feld-Zuwachs nicht geloescht werden muss.
_ADDITIVE_COLUMNS = {
    "flight_offer": [
        ("trip_type", "VARCHAR(12) DEFAULT 'one_way'"),
        ("return_date", "VARCHAR(10)"),
        ("return_segments", "JSON DEFAULT '[]'"),
        ("pax_mode", "VARCHAR(12) DEFAULT 'estimated'"),
        ("price_confidence", "VARCHAR(300) DEFAULT ''"),
    ],
    "hotel_offer": [("is_reference", "BOOLEAN DEFAULT 0")],
    "package_offer": [("is_reference", "BOOLEAN DEFAULT 0")],
    "check_run": [("ita_matrix", "JSON DEFAULT '[]'")],
}


def _migrate(engine) -> None:
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    with engine.begin() as conn:
        for table, cols in _ADDITIVE_COLUMNS.items():
            if not insp.has_table(table):
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols:
                if name not in existing:
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}'))
