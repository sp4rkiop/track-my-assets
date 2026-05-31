from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from core.deps import get_db
from schemas.tracker import OtaReleaseCreate, OtaReleaseRead, OtaJobCreate, OtaJobRead
from services.device_service import DeviceService
from services.ota_service import OtaService

router = APIRouter()


@router.post("/releases", response_model=OtaReleaseRead)
async def create_ota_release(
    release: OtaReleaseCreate, db: AsyncSession = Depends(get_db)
):
    existing = await OtaService.get_release_by_version(db, release.version)
    if existing:
        raise HTTPException(status_code=400, detail="Firmware version already exists")
    return await OtaService.create_release(db, release)


@router.post("/jobs", response_model=OtaJobRead)
async def create_ota_job(job_req: OtaJobCreate, db: AsyncSession = Depends(get_db)):
    device = await DeviceService.get_by_imei(db, job_req.imei)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    release = await OtaService.get_release_by_version(db, job_req.version)
    if not release:
        raise HTTPException(status_code=404, detail="Firmware release not found")

    return await OtaService.create_job(db, device.id, release.id)
