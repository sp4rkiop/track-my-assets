from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from models.tracker import OtaRelease, OtaJob
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
