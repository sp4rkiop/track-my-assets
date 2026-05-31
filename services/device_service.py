from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from models.tracker import Device


class DeviceService:
    @staticmethod
    async def get_by_imei(db: AsyncSession, imei: str) -> Device | None:
        result = await db.execute(select(Device).where(Device.imei == imei))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_all(db: AsyncSession, skip: int = 0, limit: int = 100):
        result = await db.execute(select(Device).offset(skip).limit(limit))
        return result.scalars().all()
