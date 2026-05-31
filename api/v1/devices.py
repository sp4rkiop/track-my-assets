from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from core.deps import get_db
from schemas.tracker import DeviceRead
from services.device_service import DeviceService

router = APIRouter()


@router.get("/", response_model=list[DeviceRead])
async def list_devices(
    skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_db)
):
    return await DeviceService.get_all(db, skip=skip, limit=limit)


@router.get("/{imei}", response_model=DeviceRead)
async def get_device(imei: str, db: AsyncSession = Depends(get_db)):
    device = await DeviceService.get_by_imei(db, imei)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return device
