from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from core.database import PostgreSQLDatabase
from models.tracker import Device, Telemetry

router = APIRouter(prefix="/telemetry", tags=["Telemetry"])


# Dependency to get DB session
async def get_db():
    async with PostgreSQLDatabase.get_session() as session:
        yield session


@router.get("/{imei}/latest")
async def get_latest_telemetry(imei: str, db: AsyncSession = Depends(get_db)):
    """Fetches the most recent coordinates for a given device."""

    # 1. Lookup Device
    device_result = await db.execute(select(Device).where(Device.imei == imei))
    device = device_result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    # 2. Lookup latest telemetry point
    tel_result = await db.execute(
        select(Telemetry)
        .where(Telemetry.device_id == device.id)
        .order_by(Telemetry.device_ts.desc())
        .limit(1)
    )
    latest = tel_result.scalar_one_or_none()

    if not latest:
        return {"message": "No telemetry data found for this device yet."}

    return {
        "device": device.name,
        "last_seen": device.last_seen,
        "coordinates": {
            "lat": float(latest.latitude) if latest.latitude else None,
            "lon": float(latest.longitude) if latest.longitude else None,
        },
        "event": latest.event_type,
        "battery": latest.battery_pct,
    }
