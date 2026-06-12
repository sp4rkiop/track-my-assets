from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.responses import FileResponse
from core.deps import get_current_user, get_db
from core.mqtt_client import MQTTService
from models.user import User
from schemas.tracker import (
    OtaReleaseCreate,
    OtaReleaseRead,
    OtaJobCreate,
    OtaJobRead,
    OtaRolloutResponse,
)
from services.device_service import DeviceService
from services.ota_service import OtaService

router = APIRouter()


@router.post("/releases", response_model=OtaReleaseRead)
async def create_ota_release(
    release: OtaReleaseCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    existing = await OtaService.get_release_by_version(db, release.version)
    if existing:
        raise HTTPException(status_code=400, detail="Firmware version already exists")
    return await OtaService.create_release(db, release)


@router.post("/jobs", response_model=OtaJobRead)
async def create_ota_job(
    job_req: OtaJobCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 1. Validate Device & Release
    device = await DeviceService.get_by_imei(db, job_req.imei)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    release = await OtaService.get_release_by_version(db, job_req.version)
    if not release:
        raise HTTPException(status_code=404, detail="Firmware release not found")

    # 2. Prevent duplicate active jobs
    # (Optional: Add a check here to ensure the device doesn't already have a 'pending' job)

    # 3. Create the Job in the Database
    job = await OtaService.create_job(db, device.id, release.id)

    # 4. THE TRIGGER: Push the OTA command to the tracker via MQTT
    command_payload = {
        "cmd": "OTA_UPDATE",
        "version": release.version,
        "url": release.firmware_url,
        "sha256": release.checksum_sha256,
    }

    published = await MQTTService.publish_command(device.imei, command_payload)

    if not published:
        # Note: We don't fail the HTTP request if MQTT fails, because QoS 1
        # offline queuing might just be waiting for the broker to reconnect.
        job.status = "failed_to_publish"
        await db.commit()

    return job


@router.post("/releases/{release_id}/rollout", response_model=OtaRolloutResponse)
async def trigger_fleet_rollout(
    release_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Triggers a bulk OTA update for all active devices that meet the firmware requirements.
    """
    release = await OtaService.get_release_by_id(db, release_id)
    if not release:
        raise HTTPException(status_code=404, detail="Release not found")

    if not release.is_stable:
        # Safety check: Prevent rolling out experimental builds to the whole fleet
        raise HTTPException(
            status_code=400, detail="Cannot bulk rollout an unstable release."
        )

    stats = await OtaService.execute_bulk_rollout(db, release)

    return OtaRolloutResponse(
        message="Fleet rollout executed successfully.",
        eligible_devices_count=stats["eligible"],
        jobs_created=stats["jobs"],
        mqtt_commands_sent=stats["mqtt"],
    )

@router.head("/download/{version}")
@router.get("/download/{version}")
async def download_firmware(version: str):
    """
    Acts as a proxy for the ESP32 to download the cached firmware binary.
    Includes self-healing: if the file was purged from cache, it is recovered on the fly.
    """
    # Ask the service layer for the file path (it will heal itself if missing)
    file_path = await OtaService.ensure_firmware_downloaded(version)

    if not file_path:
        raise HTTPException(
            status_code=404,
            detail="Firmware binary not found in cache and could not be recovered from GitHub.",
        )

    # Stream the file directly to the ESP32
    return FileResponse(
        path=file_path,
        media_type="application/octet-stream",
        filename=f"tracker-{version}.bin",
    )
