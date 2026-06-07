import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from models.base import Base


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    imei: Mapped[str] = mapped_column(
        String(15), unique=True, index=True, nullable=False
    )
    firmware_version: Mapped[str | None] = mapped_column(String, nullable=True)
    sim_iccid: Mapped[str | None] = mapped_column(String(22), nullable=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_seen: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    telemetry: Mapped[list["Telemetry"]] = relationship(
        "Telemetry", back_populates="device", cascade="all, delete-orphan"
    )
    ota_jobs: Mapped[list["OtaJob"]] = relationship(
        "OtaJob", back_populates="device", cascade="all, delete-orphan"
    )


class Telemetry(Base):
    __tablename__ = "telemetry"
    __table_args__ = (
        # TimescaleDB requires device_ts in every unique constraint / PK
        PrimaryKeyConstraint("device_id", "device_ts"),
        # Alert queries: "all BUMP events in last hour"
        Index("ix_telemetry_event_time", "event_type", "device_ts"),
    )

    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Event context
    event_type: Mapped[str | None] = mapped_column(Text, index=True)

    # device_ts — when the GPS fix happened on the hardware (from payload)
    # server_ts — when FastAPI received and ingested the MQTT message
    device_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    server_ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    # GPS — Numeric(10,7) ≈ 1 cm precision vs Float's ~1 m
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), nullable=True)
    altitude_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    speed_kmh: Mapped[float | None] = mapped_column(Float, nullable=True)
    heading_deg: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Fix quality — tells you whether to trust the coordinates
    accuracy_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    hdop: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # <2 excellent, <5 good, >10 bad
    fix_quality: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # 0=no fix, 1=GPS, 2=DGPS

    # GPS performance
    gps_ttff: Mapped[float | None] = mapped_column(Float, nullable=True)  # seconds

    # LTE signal — parsed metrics + full raw CPSI string for debugging
    rssi: Mapped[int | None] = mapped_column(Integer, nullable=True)  # dBm e.g. -85
    rsrp: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )  # reference signal power
    rsrq: Mapped[int | None] = mapped_column(Integer, nullable=True)  # signal quality
    cell_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    cpsi_raw: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Power
    battery_voltage: Mapped[float | None] = mapped_column(Float, nullable=True)
    battery_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # IMU — raw axes, precomputed magnitude, derived motion flag
    accel_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    accel_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    accel_z: Mapped[float | None] = mapped_column(Float, nullable=True)
    accel_magnitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_moving: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    device: Mapped["Device"] = relationship("Device", back_populates="telemetry")


class OtaRelease(Base):
    __tablename__ = "ota_releases"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    version: Mapped[str] = mapped_column(
        String, unique=True, nullable=False, index=True
    )
    firmware_url: Mapped[str] = mapped_column(String, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    min_firmware_version: Mapped[str | None] = mapped_column(String, nullable=True)
    is_stable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    released_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    jobs: Mapped[list["OtaJob"]] = relationship("OtaJob", back_populates="release")


class OtaJob(Base):
    __tablename__ = "ota_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    release_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        # RESTRICT: prevent deleting a release that devices are still running
        ForeignKey("ota_releases.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # State machine: pending → sent → downloading → success | failed
    status: Mapped[str] = mapped_column(
        String, default="pending", nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    device: Mapped["Device"] = relationship("Device", back_populates="ota_jobs")
    release: Mapped["OtaRelease"] = relationship("OtaRelease", back_populates="jobs")
