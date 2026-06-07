import asyncio

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from packaging.version import parse as parse_version
from core.mqtt_client import MQTTService
from models.tracker import Device, OtaRelease, OtaJob
from schemas.tracker import OtaReleaseCreate


class OtaService:
    @staticmethod
    async def create_release(
        db: AsyncSession, release_in: OtaReleaseCreate
    ) -> OtaRelease:
        db_obj = OtaRelease(**release_in.model_dump())
        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        return db_obj

    @staticmethod
    async def get_release_by_version(
        db: AsyncSession, version: str
    ) -> OtaRelease | None:
        result = await db.execute(
            select(OtaRelease).where(OtaRelease.version == version)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def create_job(db: AsyncSession, device_id, release_id) -> OtaJob:
        db_job = OtaJob(device_id=device_id, release_id=release_id, status="pending")
        db.add(db_job)
        await db.commit()
        await db.refresh(db_job)
        return db_job

    @staticmethod
    async def get_release_by_id(db: AsyncSession, release_id) -> OtaRelease | None:
        result = await db.execute(select(OtaRelease).where(OtaRelease.id == release_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def execute_bulk_rollout(db: AsyncSession, release: OtaRelease) -> dict:
        """Finds all eligible devices and executes a fleet-wide OTA rollout."""

        # 1. Fetch all ACTIVE devices
        result = await db.execute(select(Device).where(Device.is_active))
        all_active_devices = result.scalars().all()

        eligible_devices = []

        # 2. Filter using strict Semantic Versioning
        for device in all_active_devices:
            # If the release has a min constraint, check it.
            if release.min_firmware_version:
                # If the device has no firmware logged yet, skip it to be safe
                if not device.firmware_version:
                    continue

                # Compare semantic versions (e.g., handles "v1.2.10" > "v1.2.9" correctly)
                if parse_version(device.firmware_version) < parse_version(
                    release.min_firmware_version
                ):
                    continue

            # If it passes, or if there is no min constraint, add to target list
            eligible_devices.append(device)

        if not eligible_devices:
            return {"eligible": 0, "jobs": 0, "mqtt": 0}

        # 3. Create the Database Jobs (Fast batch insert)
        jobs_to_create = []
        for device in eligible_devices:
            jobs_to_create.append(
                OtaJob(device_id=device.id, release_id=release.id, status="pending")
            )

        db.add_all(jobs_to_create)
        await db.commit()  # Commit so they get UUIDs instantly

        # 4. Prepare the MQTT Command Payload
        command_payload = {
            "cmd": "OTA_UPDATE",
            "version": release.version,
            "url": release.firmware_url,
            "sha256": release.checksum_sha256,
        }

        # 5. Broadcast to the Fleet Concurrently
        # We use asyncio.gather to fire off potentially hundreds of MQTT messages
        # simultaneously without waiting for each one to complete sequentially.
        mqtt_tasks = [
            MQTTService.publish_command(device.imei, command_payload)
            for device in eligible_devices
        ]

        # publish_results will be a list of booleans (True if published, False if failed)
        publish_results = await asyncio.gather(*mqtt_tasks, return_exceptions=True)
        successful_publishes = sum(1 for res in publish_results if res is True)

        return {
            "eligible": len(eligible_devices),
            "jobs": len(jobs_to_create),
            "mqtt": successful_publishes,
        }
