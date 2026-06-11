import uuid
from fastapi import APIRouter, HTTPException
from core.database import PostgreSQLDatabase
from services.trip_service import TripService

router = APIRouter()


@router.get("/devices/{device_id}/trips")
async def get_device_trips(device_id: uuid.UUID, limit: int = 20):
    """
    Returns a list of segmented trips for a specific device.
    """
    async with PostgreSQLDatabase.get_session() as db:
        trips = await TripService.get_device_trips(db, device_id, limit)
        return {"device_id": str(device_id), "trips": trips}


@router.get("/devices/{device_id}/trips/{trip_group_id}/route")
async def get_trip_route(device_id: uuid.UUID, trip_group_id: int):
    """
    Fetches the exact array of coordinates for a specific trip to draw the polyline.
    """
    async with PostgreSQLDatabase.get_session() as db:
        coordinates = await TripService.get_trip_route(db, device_id, trip_group_id)

        if not coordinates:
            raise HTTPException(status_code=404, detail="Trip not found")

        return {"trip_id": f"{device_id}_{trip_group_id}", "path": coordinates}
