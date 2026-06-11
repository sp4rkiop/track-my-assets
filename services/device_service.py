from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from models.tracker import Device


class DeviceService:
    @staticmethod
    async def get_by_id(db: AsyncSession, device_id) -> Device | None:
        result = await db.execute(select(Device).where(Device.id == device_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_imei(db: AsyncSession, imei: str) -> Device | None:
        result = await db.execute(select(Device).where(Device.imei == imei))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_all(db: AsyncSession, skip: int = 0, limit: int = 100):
        query = (
            select(Device)
            .order_by(Device.last_seen.desc().nulls_last())
            .offset(skip)
            .limit(limit)
        )
        result = await db.execute(query)
        return result.scalars().all()

    @staticmethod
    async def update_device_name(
        db: AsyncSession, device_id: str, new_name: str
    ) -> Device | None:
        device = await db.scalar(select(Device).where(Device.id == device_id))
        if device:
            device.name = new_name
            db.add(device)
            await db.commit()
            await db.refresh(device)
        return device

    @staticmethod
    async def delete_device(db: AsyncSession, device_id: str) -> bool:
        device = await db.scalar(select(Device).where(Device.id == device_id))
        if device:
            await db.delete(device)
            await db.commit()
            return True
        return False
