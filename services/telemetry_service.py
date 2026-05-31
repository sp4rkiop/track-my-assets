from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from models.tracker import Telemetry


class TelemetryService:
    @staticmethod
    async def get_latest_by_device_id(db: AsyncSession, device_id):
        result = await db.execute(
            select(Telemetry)
            .where(Telemetry.device_id == device_id)
            .order_by(Telemetry.device_ts.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_history(db: AsyncSession, device_id, limit: int = 50):
        result = await db.execute(
            select(Telemetry)
            .where(Telemetry.device_id == device_id)
            .order_by(Telemetry.device_ts.desc())
            .limit(limit)
        )
        return result.scalars().all()
