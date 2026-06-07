from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from api.v1 import devices, ota, telemetry
from api.v1 import ipinfo
from core.config import settings
from core.database import PostgreSQLDatabase
from core.middleware import RequestIdMiddleware
from core.mqtt_client import MQTTService
from core.redis_cache import RedisCache
from services.geoip_service import close_readers, load_readers
from services.updater_service import scheduled_db_update


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- startup ---
    await RedisCache.initialize()
    await PostgreSQLDatabase.initialize()
    await MQTTService.initialize()
    await MQTTService.start_listening()

    # # Load previously downloaded files on startup if they exist
    # await load_readers()

    # # Initialize the asyncio-compatible scheduler
    # scheduler = AsyncIOScheduler()

    # # Kick off a non-blocking initial update immediately upon startup
    # scheduler.add_job(scheduled_db_update, "date", run_date=None)

    # # Schedule regular updates (Wednesdays and Saturdays at 2:00 AM)
    # scheduler.add_job(
    #     scheduled_db_update, "cron", day_of_week="wed,sat", hour=2, minute=0
    # )

    # scheduler.start()

    yield
    # --- shutdown ---
    # 3. Shutdown: Clean up background tasks and file handlers
    # scheduler.shutdown()
    # await close_readers()

    await MQTTService.close_connection()
    await PostgreSQLDatabase.close_all_connections()
    await RedisCache.close_connection()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestIdMiddleware)

# app.include_router(ipinfo.router, prefix="/api/v1/ipinfo", tags=["Ip-Info"])
app.include_router(devices.router, prefix="/api/v1/devices", tags=["Devices"])
app.include_router(telemetry.router, prefix="/api/v1/telemetry", tags=["Telemetry"])
app.include_router(ota.router, prefix="/api/v1/ota", tags=["OTA Updates"])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
