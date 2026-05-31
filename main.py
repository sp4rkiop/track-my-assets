import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.v1 import telemetry
from core.config import settings
from core.database import PostgreSQLDatabase
from core.middleware import RequestIdMiddleware
from core.mqtt_client import MQTTService
from core.redis_cache import RedisCache


async def scheduled_data_fetch() -> None:
    pass  # TODO: add periodic task logic here


async def _scheduler() -> None:
    """Runs scheduled_data_fetch every 24 hours."""
    while True:
        await asyncio.sleep(86400)
        await scheduled_data_fetch()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- startup ---
    await RedisCache.initialize()
    await PostgreSQLDatabase.initialize()
    await MQTTService.initialize()
    await MQTTService.start_listening()
    task = asyncio.create_task(_scheduler())
    yield
    # --- shutdown ---
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
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

app.include_router(telemetry.router, prefix="/api/v1/telemetry", tags=["telemetry"])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
