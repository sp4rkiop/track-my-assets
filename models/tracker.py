import uuid
from sqlalchemy import (
    Column,
    PrimaryKeyConstraint,
    String,
    Float,
    DateTime,
    ForeignKey,
    Boolean,
    Integer,
    Numeric,
    Index,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from models.base import Base


class Device(Base):
    __tablename__ = "devices"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    imei = Column(String(15), unique=True, index=True, nullable=False)

    # Added: track firmware so OTA knows what's currently running
    firmware_version = Column(String, nullable=True)
    # Added: SIM ICCID for carrier-level debugging
    sim_iccid = Column(String(22), nullable=True, index=True)

    # Added: quick health flags — avoids full telemetry scan for dashboard
    is_active = Column(Boolean, default=True, nullable=False)
    last_seen = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    telemetry = relationship(
        "Telemetry", back_populates="device", cascade="all, delete-orphan"
    )
    ota_jobs = relationship(
        "OtaJob", back_populates="device", cascade="all, delete-orphan"
    )


class Telemetry(Base):
    __tablename__ = "telemetry"
    __table_args__ = (
        # 3. Explicitly define the composite primary key
        PrimaryKeyConstraint("id", "device_ts"),
        # Timescale automatically indexes the time column, but creating a
        # composite index with device_id first is great for device-specific queries
        Index("ix_telemetry_device_time", "device_id", "device_ts"),
    )

    id = Column(UUID(as_uuid=True), default=uuid.uuid4, nullable=False)
    device_id = Column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Event context
    event_type = Column(Text, index=True)  # 'PERIODIC', 'BUMP', 'BOOT', 'SOS'

    # FIXED: two timestamps — device clock vs server ingestion time
    # device_ts = when the GPS fix happened on the hardware (from payload)
    # server_ts = when FastAPI received the MQTT message
    device_ts = Column(DateTime(timezone=True), primary_key=True, nullable=False)
    server_ts = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    # GPS — Numeric(10,7) gives 1cm precision vs Float's ~1m
    latitude = Column(Numeric(10, 7), nullable=True)
    longitude = Column(Numeric(10, 7), nullable=True)
    altitude_m = Column(Float, nullable=True)  # MSL altitude from NMEA
    speed_kmh = Column(Float, nullable=True)  # Ground speed from GNSS
    heading_deg = Column(Float, nullable=True)  # Track angle 0-360

    # Added: fix quality signals — tells you whether to trust the coordinates
    accuracy_m = Column(Float, nullable=True)  # Horizontal accuracy estimate
    hdop = Column(Integer, nullable=True)  # <2 excellent, <5 good, >10 bad
    fix_quality = Column(Integer, nullable=True)  # 0=no fix, 1=GPS, 2=DGPS

    # GPS performance
    gps_ttff = Column(Float, nullable=True)  # Time to first fix (seconds)

    # Cell signal — critical for LTE-only fallback debugging
    # Added: parsed signal metrics alongside raw CPSI string
    rssi = Column(Integer, nullable=True)  # dBm, e.g. -85
    rsrp = Column(Integer, nullable=True)  # LTE reference signal power
    rsrq = Column(Integer, nullable=True)  # LTE signal quality
    cell_id = Column(Text, nullable=True, index=True)  # for cell-based fallback
    cpsi_raw = Column(Text, nullable=True)  # full raw string for debugging

    # Power
    battery_voltage = Column(Float, nullable=True)
    battery_pct = Column(Float, nullable=True)  # Added: derived % (0.0–100.0)

    # IMU — raw axes + precomputed magnitude
    accel_x = Column(Float, nullable=True)
    accel_y = Column(Float, nullable=True)
    accel_z = Column(Float, nullable=True)
    # Added: sqrt(x²+y²+z²) — store it so queries don't compute it every time
    accel_magnitude = Column(Float, nullable=True)
    # Added: derived motion flag (magnitude > threshold) — fast filter for HA
    is_moving = Column(Boolean, nullable=True)

    device = relationship("Device", back_populates="telemetry")

    # Composite indexes for the queries you'll actually run
    __table_args__ = (
        # Dashboard: "latest N points for device X in time range"
        Index("ix_telemetry_device_time", "device_id", "device_ts"),
        # Alert queries: "all BUMP events in last hour"
        Index("ix_telemetry_event_time", "event_type", "device_ts"),
    )


class OtaRelease(Base):
    __tablename__ = "ota_releases"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version = Column(String, unique=True, nullable=False, index=True)
    firmware_url = Column(String, nullable=False)  # presigned S3 or VPS path
    checksum_sha256 = Column(String(64), nullable=False)  # ESP32 verifies this
    is_stable = Column(Boolean, default=False)  # promote manually
    released_at = Column(DateTime(timezone=True), server_default=func.now())

    jobs = relationship("OtaJob", back_populates="release")


class OtaJob(Base):
    __tablename__ = "ota_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id = Column(
        UUID(as_uuid=True),
        ForeignKey("devices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    release_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ota_releases.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # 'pending' → 'sent' → 'downloading' → 'success' | 'failed'
    status = Column(String, default="pending", nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    attempt_count = Column(Integer, default=0)

    device = relationship("Device", back_populates="ota_jobs")
    release = relationship("OtaRelease", back_populates="jobs")
