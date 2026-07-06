"""ORM models mirroring db/migrations/001_init.sql.

Migrations own the DDL; these models exist for application reads/writes and
are never used to create tables.
"""

from datetime import date, datetime

from geoalchemy2 import Geography
from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    Computed,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SourceIngestRun(Base):
    __tablename__ = "source_ingest_runs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    source: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, default="running")
    record_count: Mapped[int | None] = mapped_column(Integer)
    source_version: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)


class Vessel(Base):
    __tablename__ = "vessels"

    imo: Mapped[str] = mapped_column(CHAR(7), primary_key=True)
    name: Mapped[str | None] = mapped_column(Text)
    callsign: Mapped[str | None] = mapped_column(Text)
    flag: Mapped[str | None] = mapped_column(CHAR(3))
    vessel_type: Mapped[str | None] = mapped_column(Text)
    gross_tonnage: Mapped[float | None] = mapped_column(Numeric)
    length_m: Mapped[float | None] = mapped_column(Numeric)
    gfw_vessel_id: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class VesselIdentity(Base):
    __tablename__ = "vessel_identities"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    imo: Mapped[str] = mapped_column(CHAR(7), ForeignKey("vessels.imo"))
    mmsi: Mapped[str | None] = mapped_column(CHAR(9))
    name: Mapped[str | None] = mapped_column(Text)
    callsign: Mapped[str | None] = mapped_column(Text)
    flag: Mapped[str | None] = mapped_column(CHAR(3))
    source: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class VesselFlagHistory(Base):
    __tablename__ = "vessel_flag_history"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    imo: Mapped[str] = mapped_column(CHAR(7), ForeignKey("vessels.imo"))
    flag: Mapped[str] = mapped_column(CHAR(3))
    source: Mapped[str] = mapped_column(Text)
    observed_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    observed_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class VesselOwnership(Base):
    __tablename__ = "vessel_ownership"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    imo: Mapped[str] = mapped_column(CHAR(7), ForeignKey("vessels.imo"))
    owner_name: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text, default="registered_owner")
    country: Mapped[str | None] = mapped_column(CHAR(3))
    source: Mapped[str] = mapped_column(Text)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class AisPosition(Base):
    __tablename__ = "ais_positions"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    mmsi: Mapped[str] = mapped_column(CHAR(9))
    imo: Mapped[str | None] = mapped_column(CHAR(7), ForeignKey("vessels.imo"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    position = mapped_column(Geography("POINT", srid=4326))
    sog_knots: Mapped[float | None] = mapped_column(Float)
    cog_deg: Mapped[float | None] = mapped_column(Float)
    heading_deg: Mapped[float | None] = mapped_column(Float)
    nav_status: Mapped[int | None] = mapped_column(SmallInteger)
    source: Mapped[str] = mapped_column(Text)
    raw: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class AisGap(Base):
    __tablename__ = "ais_gaps"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    imo: Mapped[str | None] = mapped_column(CHAR(7), ForeignKey("vessels.imo"))
    mmsi: Mapped[str | None] = mapped_column(CHAR(9))
    gap_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    gap_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    start_position = mapped_column(Geography("POINT", srid=4326))
    end_position = mapped_column(Geography("POINT", srid=4326))
    duration_hours: Mapped[float | None] = mapped_column(
        Numeric, Computed("EXTRACT(epoch FROM (gap_end - gap_start)) / 3600.0", persisted=True)
    )
    source: Mapped[str] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class GfwEvent(Base):
    __tablename__ = "gfw_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    gfw_event_id: Mapped[str] = mapped_column(Text, unique=True)
    event_type: Mapped[str] = mapped_column(Text)
    imo: Mapped[str | None] = mapped_column(CHAR(7), ForeignKey("vessels.imo"))
    gfw_vessel_id: Mapped[str | None] = mapped_column(Text)
    other_imo: Mapped[str | None] = mapped_column(CHAR(7))
    other_gfw_vessel_id: Mapped[str | None] = mapped_column(Text)
    start_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    geom = mapped_column(Geography("POINT", srid=4326))
    raw: Mapped[dict] = mapped_column(JSONB)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class SdnEntity(Base):
    __tablename__ = "sdn_entities"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    sdn_uid: Mapped[int] = mapped_column(Integer)
    list_date: Mapped[date] = mapped_column(Date)
    name: Mapped[str] = mapped_column(Text)
    sdn_type: Mapped[str | None] = mapped_column(Text)
    programs: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    vessel_imo: Mapped[str | None] = mapped_column(CHAR(7))
    vessel_callsign: Mapped[str | None] = mapped_column(Text)
    vessel_flag: Mapped[str | None] = mapped_column(Text)
    vessel_owner: Mapped[str | None] = mapped_column(Text)
    remarks: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict] = mapped_column(JSONB)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class SdnAltName(Base):
    __tablename__ = "sdn_alt_names"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    sdn_uid: Mapped[int] = mapped_column(Integer)
    list_date: Mapped[date] = mapped_column(Date)
    alt_name: Mapped[str] = mapped_column(Text)
    alt_type: Mapped[str | None] = mapped_column(Text)


class SdnMatch(Base):
    __tablename__ = "sdn_matches"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    imo: Mapped[str] = mapped_column(CHAR(7), ForeignKey("vessels.imo"))
    sdn_uid: Mapped[int] = mapped_column(Integer)
    match_type: Mapped[str] = mapped_column(Text)
    score: Mapped[float] = mapped_column(Float)
    matched_value: Mapped[str] = mapped_column(Text)
    sdn_value: Mapped[str] = mapped_column(Text)
    list_date: Mapped[date] = mapped_column(Date)
    first_matched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    review_status: Mapped[str] = mapped_column(Text, default="unreviewed")
    reviewed_by: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RiskRule(Base):
    __tablename__ = "risk_rules"

    rule_id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    regime: Mapped[str] = mapped_column(Text, default="OFAC")
    version: Mapped[int] = mapped_column(Integer, default=1)
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class Screening(Base):
    __tablename__ = "screenings"

    id = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    imo: Mapped[str] = mapped_column(CHAR(7), ForeignKey("vessels.imo"))
    screened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    requested_by: Mapped[str | None] = mapped_column(Text)
    trigger: Mapped[str] = mapped_column(Text, default="manual")
    ruleset: Mapped[dict] = mapped_column(JSONB)
    data_sources: Mapped[dict] = mapped_column(JSONB)
    overall_status: Mapped[str] = mapped_column(Text)
    report_json: Mapped[dict] = mapped_column(JSONB)
    report_pdf_path: Mapped[str | None] = mapped_column(Text)
    report_sha256: Mapped[str | None] = mapped_column(CHAR(64))


class ScreeningFinding(Base):
    __tablename__ = "screening_findings"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    screening_id = mapped_column(UUID(as_uuid=True), ForeignKey("screenings.id"))
    rule_id: Mapped[str] = mapped_column(Text, ForeignKey("risk_rules.rule_id"))
    severity: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict)


class WatchlistEntry(Base):
    __tablename__ = "watchlist_entries"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    imo: Mapped[str] = mapped_column(CHAR(7), ForeignKey("vessels.imo"))
    label: Mapped[str | None] = mapped_column(Text)
    alert_email: Mapped[str] = mapped_column(Text)
    rescreen_interval_hours: Mapped[int] = mapped_column(Integer, default=24)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class WatchlistAlert(Base):
    __tablename__ = "watchlist_alerts"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    watchlist_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("watchlist_entries.id"))
    screening_id = mapped_column(UUID(as_uuid=True), ForeignKey("screenings.id"))
    channel: Mapped[str] = mapped_column(Text, default="email")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, default="pending")
