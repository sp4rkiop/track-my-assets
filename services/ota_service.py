import asyncio
from datetime import datetime
import time
import uuid
import aiofiles
import os

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from packaging.version import parse as parse_version
from sqlalchemy.orm import joinedload
from core.config import settings
from core.database import PostgreSQLDatabase
from core.mqtt_client import MQTTService
from core.logger import get_logger
from models.tracker import Device, OtaRelease, OtaJob
from schemas.tracker import OtaReleaseCreate

logger = get_logger(__name__)
CACHE_DIR = "/tmp/fleet_os_ota_cache"
os.makedirs(CACHE_DIR, exist_ok=True)


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
    async def delete_job(db: AsyncSession, job_id: str) -> bool:
        """Deletes an OTA job from the database."""
        # Using UUID explicitly in case the string needs casting, but SQLAlchemy usually handles it
        result = await db.execute(select(OtaJob).where(OtaJob.id == job_id))
        job = result.scalar_one_or_none()

        if job:
            await db.delete(job)
            await db.commit()
            return True
        return False

    @staticmethod
    async def get_release_by_id(db: AsyncSession, release_id) -> OtaRelease | None:
        result = await db.execute(select(OtaRelease).where(OtaRelease.id == release_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_all_releases(db: AsyncSession, skip: int = 0, limit: int = 20):
        result = await db.execute(
            select(OtaRelease)
            .order_by(OtaRelease.released_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    @staticmethod
    async def get_recent_jobs(db: AsyncSession, skip: int = 0, limit: int = 20):
        result = await db.execute(
            select(OtaJob)
            .options(joinedload(OtaJob.device), joinedload(OtaJob.release))
            .order_by(OtaJob.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return result.scalars().all()

    @staticmethod
    async def execute_single_rollout(
        db: AsyncSession, release: OtaRelease, device: Device
    ) -> OtaJob:
        """Pushes an OTA update to a single canary device."""
        job = OtaJob(device_id=device.id, release_id=release.id, status="pending")
        db.add(job)
        await db.commit()
        await db.refresh(job)

        command_payload = {
            "cmd": "OTA_UPDATE",
            "version": release.version,
            "url": release.firmware_url,
            "sha256": release.checksum_sha256,
        }
        success = await MQTTService.publish_command(device.imei, command_payload)

        if not success:
            job.status = "failed_to_publish"
            await db.commit()

        return job

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

    @staticmethod
    async def ensure_firmware_downloaded(version: str) -> str | None:
        """
        Self-Healing Proxy: Checks if the firmware exists locally.
        If deleted by the cleanup job, it re-downloads it on the fly from GitHub using an atomic write.
        """
        bin_filename = f"tracker-{version}.bin"
        local_path = os.path.join(CACHE_DIR, bin_filename)

        # 1. Best case scenario: The file is already on disk
        if os.path.exists(local_path):
            return local_path

        logger.warning(
            f"Firmware {version} missing from cache. Triggering self-healing recovery..."
        )

        # 2. Query GitHub for the specific missing tag
        repo_url = f"https://api.github.com/repos/sp4rkiop/track-my-assets-fw/releases/tags/{version}"
        headers = {
            "Authorization": f"Bearer {settings.GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
        }

        async with httpx.AsyncClient(follow_redirects=True) as client:
            tmp_path: str | None = None
            try:
                response = await client.get(repo_url, headers=headers)
                if response.status_code == 404:
                    logger.error(
                        f"Version {version} no longer exists on GitHub. Cannot recover."
                    )
                    return None

                response.raise_for_status()
                data = response.json()

                # Find the binary URL
                bin_url = None
                for asset in data.get("assets", []):
                    if asset["name"].endswith(".bin"):
                        bin_url = asset["url"]
                        break

                if not bin_url:
                    logger.error(
                        f"No .bin asset found attached to GitHub release {version}."
                    )
                    return None

                logger.info("Found missing asset. Streaming from GitHub...")

                bin_response = await client.get(
                    bin_url,
                    headers={
                        "Authorization": headers["Authorization"],
                        "Accept": "application/octet-stream",
                    },
                )
                bin_response.raise_for_status()

                # 3. ATOMIC WRITE: Write to a unique .tmp file first
                # This prevents file corruption if multiple devices trigger recovery simultaneously
                tmp_path = f"{local_path}.{uuid.uuid4().hex}.tmp"

                async with aiofiles.open(tmp_path, "wb") as f:
                    await f.write(bin_response.content)

                # 4. Swap the temp file to the real file path instantly
                os.replace(tmp_path, local_path)

                logger.info(
                    f"Self-healing complete. Firmware {version} fully restored to disk cache."
                )
                return local_path

            except Exception as e:
                logger.error(
                    f"Failed to recover firmware {version}: {e}", exc_info=True
                )

                # Clean up the temp file if the download crashed mid-way
                if tmp_path is not None and os.path.exists(tmp_path):
                    os.remove(tmp_path)

                return None

    @staticmethod
    async def sync_github_releases():
        """
        Background task: Polls GitHub for the releases.
        Downloads missing binaries and updates the database.
        """
        logger.info("Starting GitHub OTA Sync...")

        repo_url = "https://api.github.com/repos/sp4rkiop/track-my-assets-fw/releases"
        headers = {"Authorization": f"Bearer {settings.GITHUB_TOKEN}"}

        async with httpx.AsyncClient(follow_redirects=True) as client:
            try:
                response = await client.get(repo_url, headers=headers)

                # If no releases exist yet, GitHub returns 404
                if response.status_code == 404:
                    logger.info("No releases found in GitHub repo.")
                    return

                response.raise_for_status()
                releases_data = response.json()  # Now a list of releases

                async with PostgreSQLDatabase.get_session() as db:
                    for data in releases_data:
                        version_tag = data.get("tag_name")

                        # 1. Check if we already have this specific release
                        existing = await db.execute(
                            select(OtaRelease).where(OtaRelease.version == version_tag)
                        )
                        if existing.scalar_one_or_none():
                            continue  # Skip to the next release in the loop

                        logger.info(
                            f"New release {version_tag} detected. Processing assets..."
                        )

                        # 2. Extract asset URLs
                        bin_url = sha_url = None
                        for asset in data.get("assets", []):
                            if asset["name"].endswith(".bin"):
                                bin_url = asset["url"]
                            elif asset["name"].endswith(".sha256"):
                                sha_url = asset["url"]

                        if not bin_url or not sha_url:
                            logger.warning(
                                f"Release {version_tag} missing assets. Skipping."
                            )
                            continue

                        # 3. Download the .sha256 checksum
                        sha_response = await client.get(
                            sha_url,
                            headers={
                                "Authorization": headers["Authorization"],
                                "Accept": "application/octet-stream",
                            },
                        )
                        sha_response.raise_for_status()
                        checksum = sha_response.text.strip().split()[0]

                        # 4. Download the .bin file
                        bin_filename = f"tracker-{version_tag}.bin"
                        local_path = os.path.join(CACHE_DIR, bin_filename)

                        logger.info(
                            f"Downloading firmware {version_tag} to {local_path}..."
                        )
                        bin_response = await client.get(
                            bin_url,
                            headers={
                                "Authorization": headers["Authorization"],
                                "Accept": "application/octet-stream",
                            },
                        )
                        bin_response.raise_for_status()

                        async with aiofiles.open(local_path, "wb") as f:
                            await f.write(bin_response.content)

                        # 5. Insert into Database
                        proxy_url = (
                            f"{settings.BASE_URL}/api/v1/ota/download/{version_tag}"
                        )
                        new_release = OtaRelease(
                            version=version_tag,
                            firmware_url=proxy_url,
                            checksum_sha256=checksum,
                            is_stable=True,
                            released_at=datetime.fromisoformat(
                                data["published_at"].replace("Z", "+00:00")
                            ),
                        )

                        db.add(new_release)
                        await db.commit()
                        logger.info(f"Successfully synced Release {version_tag}.")

            except Exception as e:
                logger.error(f"Failed to sync GitHub releases: {e}", exc_info=True)

    @staticmethod
    async def cleanup_old_cache_files():
        """
        Deletes firmware binaries from the local disk that are older than 30 days.
        This prevents the server's hard drive from filling up over time.
        """
        logger.info("Running routine OTA cache cleanup...")
        thirty_days_ago = time.time() - (30 * 24 * 60 * 60)
        deleted_count = 0

        try:
            for filename in os.listdir(CACHE_DIR):
                file_path = os.path.join(CACHE_DIR, filename)

                # Check if it's a file and if its last modified time is older than 30 days
                if os.path.isfile(file_path):
                    file_modified_time = os.path.getmtime(file_path)

                    if file_modified_time < thirty_days_ago:
                        os.remove(file_path)
                        deleted_count += 1
                        logger.info(f"Deleted old cached firmware: {filename}")

            logger.info(
                f"OTA cache cleanup complete. Removed {deleted_count} old files."
            )
        except Exception as e:
            logger.error(f"Error during cache cleanup: {e}", exc_info=True)
