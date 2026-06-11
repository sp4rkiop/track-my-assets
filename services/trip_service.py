import uuid
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class TripService:
    @staticmethod
    async def get_device_trips(
        db: AsyncSession, device_id: uuid.UUID, skip: int = 0, limit: int = 20
    ) -> list[dict]:
        """
        Uses PostgreSQL Window Functions to dynamically group telemetry points into trips.
        A new trip starts when there is a 'BUMP' event OR a time gap of more than 5 minutes.
        """
        query = text("""
            WITH lagged_data AS (
                SELECT 
                    device_ts, 
                    event_type, 
                    speed_kmh, 
                    latitude, 
                    longitude,
                    LAG(device_ts) OVER (ORDER BY device_ts) as prev_ts
                FROM telemetry
                WHERE device_id = :device_id 
                  AND latitude IS NOT NULL 
                  AND longitude IS NOT NULL
            ),
            trip_markers AS (
                SELECT *,
                    CASE 
                        WHEN prev_ts IS NULL THEN 1
                        WHEN EXTRACT(EPOCH FROM (device_ts - prev_ts)) > 300 THEN 1
                        WHEN event_type = 'BUMP' THEN 1
                        ELSE 0
                    END as is_new_trip
                FROM lagged_data
            ),
            trip_groups AS (
                SELECT *,
                    SUM(is_new_trip) OVER (ORDER BY device_ts) as trip_group_id
                FROM trip_markers
            )
            SELECT 
                trip_group_id,
                MIN(device_ts) as start_time,
                MAX(device_ts) as end_time,
                MAX(speed_kmh) as max_speed,
                COUNT(*) as point_count,
                (ARRAY_AGG(latitude ORDER BY device_ts ASC))[1] as start_lat,
                (ARRAY_AGG(longitude ORDER BY device_ts ASC))[1] as start_lon,
                (ARRAY_AGG(latitude ORDER BY device_ts DESC))[1] as end_lat,
                (ARRAY_AGG(longitude ORDER BY device_ts DESC))[1] as end_lon
            FROM trip_groups
            GROUP BY trip_group_id
            HAVING COUNT(*) > 2  -- Filter out noise (trips with only 1 or 2 points)
            ORDER BY start_time DESC
            OFFSET :skip
            LIMIT :limit;
        """)

        result = await db.execute(
            query, {"device_id": device_id, "skip": skip, "limit": limit}
        )
        rows = result.fetchall()

        trips = []
        for row in rows:
            # Calculate duration in minutes
            duration_delta = row.end_time - row.start_time
            duration_mins = round(duration_delta.total_seconds() / 60, 1)

            trips.append(
                {
                    "trip_id": f"{device_id}_{row.trip_group_id}",  # Pseudo-ID for frontend routing
                    "start_time": row.start_time.isoformat(),
                    "end_time": row.end_time.isoformat(),
                    "duration_mins": duration_mins,
                    "max_speed_kmh": round(row.max_speed, 1) if row.max_speed else 0.0,
                    "data_points": row.point_count,
                    "start_coord": [float(row.start_lat), float(row.start_lon)],
                    "end_coord": [float(row.end_lat), float(row.end_lon)],
                }
            )

        return trips

    @staticmethod
    async def get_trip_route(
        db: AsyncSession, device_id: uuid.UUID, trip_group_id: int
    ) -> list[dict] | None:
        """
        Fetches the exact array of coordinates for a specific trip to draw the polyline.
        """
        query = text("""
            WITH lagged_data AS (
                SELECT 
                    device_ts, 
                    latitude, 
                    longitude,
                    speed_kmh,
                    LAG(device_ts) OVER (ORDER BY device_ts) as prev_ts,
                    event_type
                FROM telemetry
                WHERE device_id = :device_id 
                  AND latitude IS NOT NULL 
                  AND longitude IS NOT NULL
            ),
            trip_markers AS (
                SELECT *,
                    CASE 
                        WHEN prev_ts IS NULL THEN 1
                        WHEN EXTRACT(EPOCH FROM (device_ts - prev_ts)) > 300 THEN 1
                        WHEN event_type = 'BUMP' THEN 1
                        ELSE 0
                    END as is_new_trip
                FROM lagged_data
            ),
            trip_groups AS (
                SELECT *,
                    SUM(is_new_trip) OVER (ORDER BY device_ts) as current_group_id
                FROM trip_markers
            )
            SELECT 
                latitude, 
                longitude, 
                speed_kmh,
                device_ts
            FROM trip_groups
            WHERE current_group_id = :trip_group_id
            ORDER BY device_ts ASC;
        """)

        result = await db.execute(
            query, {"device_id": device_id, "trip_group_id": trip_group_id}
        )
        rows = result.fetchall()

        if not rows:
            return None

        # Format as a GeoJSON LineString equivalent or simple LatLng array for Leaflet
        coordinates = []
        for row in rows:
            coordinates.append(
                {
                    "lat": float(row.latitude),
                    "lng": float(row.longitude),
                    "speed": float(row.speed_kmh) if row.speed_kmh else 0.0,
                    "timestamp": row.device_ts.isoformat(),
                }
            )

        return coordinates
