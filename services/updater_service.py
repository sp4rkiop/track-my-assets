import httpx
import tarfile
import io
import os
import shutil
import asyncio
from core.config import settings
from core.logger import get_logger
from services.geoip_service import load_readers, ASN_DB_PATH, CITY_DB_PATH

logger = get_logger(__name__)


def _extract_mmdb(content: bytes, dest_path: str) -> bool:
    """Synchronous CPU/Disk-bound extraction logic to be run in a thread."""
    with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as tar:
        for member in tar.getmembers():
            if member.name.endswith(".mmdb"):
                f = tar.extractfile(member)

                # Fixes the basedpyright type error
                if f is None:
                    continue

                temp_path = dest_path + ".tmp"
                with open(temp_path, "wb") as out:
                    shutil.copyfileobj(f, out)

                os.replace(temp_path, dest_path)
                return True
    return False


async def download_and_extract(edition_id: str, dest_path: str, license_key: str):
    url = f"https://download.maxmind.com/app/geoip_download?edition_id={edition_id}&license_key={license_key}&suffix=tar.gz"

    async with httpx.AsyncClient() as client:
        # Non-blocking network request
        response = await client.get(url, follow_redirects=True)
        response.raise_for_status()

        # Offload blocking extraction to a worker thread
        success = await asyncio.to_thread(_extract_mmdb, response.content, dest_path)
        if not success:
            raise Exception(
                f"Failed to find .mmdb file in the downloaded archive for {edition_id}"
            )


async def scheduled_db_update():
    license_key = settings.MAXMIND_LICENSE_KEY
    if not license_key:
        logger.critical(
            "MAXMIND_LICENSE_KEY environment variable not set. Skipping DB updates."
        )
        return

    logger.info("Downloading latest GeoLite2 databases...")
    try:
        os.makedirs(os.path.dirname(ASN_DB_PATH), exist_ok=True)
        await download_and_extract("GeoLite2-ASN", ASN_DB_PATH, license_key)
        await download_and_extract("GeoLite2-City", CITY_DB_PATH, license_key)

        logger.info("Databases downloaded successfully. Reloading readers...")
        await load_readers()
    except Exception as e:
        logger.exception(f"Error updating databases: {e}")
