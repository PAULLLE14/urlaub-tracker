"""SQLAlchemy-Modelle fuer die Preishistorie.

Ein ``CheckRun`` = ein Durchlauf des Schedulers.  Alle bei diesem Durchlauf
gefundenen Angebote haengen daran; ``BestSnapshot`` speichert die verdichteten
Kennzahlen fuer Trend-Charts und Ampel.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CheckRun(Base):
    __tablename__ = "check_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|ok|partial|error
    trigger: Mapped[str] = mapped_column(String(20), default="schedule")
    notes: Mapped[str] = mapped_column(Text, default="")
    source_health: Mapped[dict] = mapped_column(JSON, default=dict)  # {source: {ok, count, error}}
    # ITA Matrix ist NICHT buchbar - bewusst getrennt von flights (siehe
    # sources/flights_ita_matrix.py), simple JSON-Liste statt eigener Tabelle:
    # kleine Menge (1 Zeile je Heimatflughafen), reine Recherche-Referenz.
    ita_matrix: Mapped[list] = mapped_column(JSON, default=list)

    flights: Mapped[list["FlightOfferRow"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    hotels: Mapped[list["HotelOfferRow"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    packages: Mapped[list["PackageOfferRow"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    snapshot: Mapped["BestSnapshot | None"] = relationship(
        back_populates="run", cascade="all, delete-orphan", uselist=False
    )


class FlightOfferRow(Base):
    __tablename__ = "flight_offer"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("check_run.id", ondelete="CASCADE"), index=True)
    run: Mapped[CheckRun] = relationship(back_populates="flights")

    source: Mapped[str] = mapped_column(String(30))
    direction: Mapped[str] = mapped_column(String(12), index=True)  # outbound|return|round_trip
    trip_type: Mapped[str] = mapped_column(String(12), default="one_way", index=True)
    origin: Mapped[str] = mapped_column(String(4), index=True)
    destination: Mapped[str] = mapped_column(String(4))
    search_date: Mapped[str] = mapped_column(String(10), index=True)
    return_date: Mapped[str | None] = mapped_column(String(10))

    price_total: Mapped[float] = mapped_column(Float)
    price_per_person: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(4), default="EUR")

    airlines: Mapped[list] = mapped_column(JSON, default=list)
    segments: Mapped[list] = mapped_column(JSON, default=list)
    return_segments: Mapped[list] = mapped_column(JSON, default=list)
    stops: Mapped[int] = mapped_column(Integer, default=0)
    layover_airports: Mapped[list] = mapped_column(JSON, default=list)
    layover_minutes: Mapped[list] = mapped_column(JSON, default=list)
    total_duration_minutes: Mapped[int] = mapped_column(Integer, default=0)
    carbon_grams: Mapped[int | None] = mapped_column(Integer)

    deep_link: Mapped[str] = mapped_column(Text, default="")
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    excluded: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    exclude_reason: Mapped[str] = mapped_column(String(120), default="")

    # "estimated" (1-Pax-Suche x Personenzahl hochgerechnet) oder "group"
    # (echte Suche mit der vollen Personenzahl - deckt Faelle auf, in denen
    # der guenstige 1-Pax-Preis fuer die Gruppe gar nicht verfuegbar ist).
    # Zuordnung zum estimated-Angebot ueber die Route/Datums-Kombination,
    # nicht ueber eine FK (die id steht vor dem Insert noch nicht fest).
    pax_mode: Mapped[str] = mapped_column(String(12), default="estimated", index=True)
    # Nur fuer pax_mode="split_4_4" gesetzt - erklaert, ob der Preis eine
    # reine Untergrenze oder eine konservative Schaetzung ist (siehe
    # offers.py FlightOffer.price_confidence).
    price_confidence: Mapped[str] = mapped_column(String(300), default="")
    # Siehe offers.py FlightOffer.segment_times_approximate - steuert im
    # Dashboard, ob Zwischenzeiten/Umstiegsdauern angezeigt werden duerfen
    # (Roadmap Runde 2, Punkt 1.3/1.4).
    segment_times_approximate: Mapped[bool] = mapped_column(Boolean, default=False)


class HotelOfferRow(Base):
    __tablename__ = "hotel_offer"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("check_run.id", ondelete="CASCADE"), index=True)
    run: Mapped[CheckRun] = relationship(back_populates="hotels")

    source: Mapped[str] = mapped_column(String(60), index=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    price_total: Mapped[float | None] = mapped_column(Float)
    per_night: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(4), default="EUR")
    room_desc: Mapped[str] = mapped_column(Text, default="")
    nights: Mapped[int] = mapped_column(Integer, default=0)
    rooms: Mapped[int] = mapped_column(Integer, default=0)
    guests: Mapped[int] = mapped_column(Integer, default=0)
    deep_link: Mapped[str] = mapped_column(Text, default="")
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    error: Mapped[str] = mapped_column(Text, default="")
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    is_reference: Mapped[bool] = mapped_column(Boolean, default=False)


class PackageOfferRow(Base):
    __tablename__ = "package_offer"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("check_run.id", ondelete="CASCADE"), index=True)
    run: Mapped[CheckRun] = relationship(back_populates="packages")

    source: Mapped[str] = mapped_column(String(60), index=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    price_total: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(4), default="EUR")
    operator: Mapped[str] = mapped_column(String(60), default="")
    hotel_name: Mapped[str] = mapped_column(Text, default="")
    dep_airport: Mapped[str] = mapped_column(String(8), default="")
    board: Mapped[str] = mapped_column(String(40), default="")
    nights: Mapped[int] = mapped_column(Integer, default=0)
    persons: Mapped[int] = mapped_column(Integer, default=0)
    deep_link: Mapped[str] = mapped_column(Text, default="")
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    error: Mapped[str] = mapped_column(Text, default="")
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    is_reference: Mapped[bool] = mapped_column(Boolean, default=False)


class BestSnapshot(Base):
    """Verdichtete Kennzahlen pro Check - Basis fuer Charts & Ampel."""

    __tablename__ = "best_snapshot"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("check_run.id", ondelete="CASCADE"), unique=True)
    run: Mapped[CheckRun] = relationship(back_populates="snapshot")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    currency: Mapped[str] = mapped_column(String(4), default="EUR")

    # Guenstigster Flug (Hin+Rueck, mix & match Flughaefen), 8 Pax
    flight_out_id: Mapped[int | None] = mapped_column(Integer)
    flight_ret_id: Mapped[int | None] = mapped_column(Integer)
    flight_total: Mapped[float | None] = mapped_column(Float)

    hotel_id: Mapped[int | None] = mapped_column(Integer)
    hotel_total: Mapped[float | None] = mapped_column(Float)

    separate_total: Mapped[float | None] = mapped_column(Float)  # Flug + Hotel getrennt

    package_id: Mapped[int | None] = mapped_column(Integer)
    package_total: Mapped[float | None] = mapped_column(Float)

    verdict: Mapped[dict] = mapped_column(JSON, default=dict)  # gefuellte Vergleichslogik


class AlertLog(Base):
    """Damit nicht bei jedem Check erneut alarmiert wird."""

    __tablename__ = "alert_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(30), index=True)
    price: Mapped[float] = mapped_column(Float)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    channel: Mapped[str] = mapped_column(String(20), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
