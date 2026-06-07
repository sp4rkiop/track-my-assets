from pydantic import BaseModel, ConfigDict
from datetime import datetime
from uuid import UUID
from typing import Optional


# --- DEVICE SCHEMAS ---
class DeviceBase(BaseModel):
    name: str
    imei: str
    sim_iccid: Optional[str] = None
    is_active: bool = True


class DeviceRead(DeviceBase):
    id: UUID
    firmware_version: Optional[str] = None
    last_seen: Optional[datetime] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


# --- TELEMETRY SCHEMAS ---
class TelemetryCoordinates(BaseModel):
    lat: float | None
    lon: float | None


class TelemetryRead(BaseModel):
    device_id: UUID
    device_ts: datetime
    event_type: str | None
    coordinates: TelemetryCoordinates
    battery_pct: float | None
    speed_kmh: float | None

    @classmethod
    def from_orm_model(cls, db_obj):
        return cls(
            device_id=db_obj.device_id,
            device_ts=db_obj.device_ts,
            event_type=db_obj.event_type,
            coordinates=TelemetryCoordinates(lat=db_obj.latitude, lon=db_obj.longitude),
            battery_pct=db_obj.battery_pct,
            speed_kmh=db_obj.speed_kmh,
        )


# --- OTA SCHEMAS ---
class OtaReleaseCreate(BaseModel):
    version: str
    firmware_url: str
    checksum_sha256: str
    min_firmware_version: str | None = None
    is_stable: bool = False


class OtaRolloutResponse(BaseModel):
    message: str
    eligible_devices_count: int
    jobs_created: int
    mqtt_commands_sent: int


class OtaReleaseRead(OtaReleaseCreate):
    id: UUID
    released_at: datetime
    model_config = ConfigDict(from_attributes=True)


class OtaJobCreate(BaseModel):
    imei: str
    version: str


class OtaJobRead(BaseModel):
    id: UUID
    device_id: UUID
    release_id: UUID
    status: str
    created_at: datetime
    completed_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)
