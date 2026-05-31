from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from core.deps import get_db
from schemas.tracker import TelemetryRead
from services.device_service import DeviceService
from services.telemetry_service import TelemetryService

router = APIRouter()


@router.get("/{imei}/latest", response_model=TelemetryRead)
async def get_latest_telemetry(imei: str, db: AsyncSession = Depends(get_db)):
    device = await DeviceService.get_by_imei(db, imei)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    latest = await TelemetryService.get_latest_by_device_id(db, device.id)
    if not latest:
        raise HTTPException(status_code=404, detail="No telemetry data found")

    return TelemetryRead.from_orm_model(latest)


@router.get("/{imei}/history", response_model=list[TelemetryRead])
async def get_telemetry_history(
    imei: str, limit: int = 50, db: AsyncSession = Depends(get_db)
):
    device = await DeviceService.get_by_imei(db, imei)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    history = await TelemetryService.get_history(db, device.id, limit)
    return [TelemetryRead.from_orm_model(h) for h in history]
