from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import pathlib

from core.database import PostgreSQLDatabase
from services.device_service import DeviceService
from services.ota_service import OtaService
from services.telemetry_service import TelemetryService

# Set up the Jinja2 template engine
BASE_DIR = pathlib.Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

web_router = APIRouter(prefix="/web", tags=["Frontend"])


@web_router.get("/", response_class=HTMLResponse)
async def get_dashboard_home(request: Request):
    """Renders the comprehensive production system metrics overview dashboard."""
    async with PostgreSQLDatabase.get_session() as db:
        # Fetch actual metrics via domain services
        devices = await DeviceService.get_all(db)
        releases = await OtaService.get_all_releases(db)
        recent_jobs = await OtaService.get_recent_jobs(db, limit=5)

        # Process counts and target tracking states cleanly
        total_count = len(devices)
        online_count = 0
        now = datetime.now(timezone.utc)

        for d in devices:
            if d.last_seen and (now - d.last_seen) < timedelta(minutes=5):
                online_count += 1

        latest_fw = "None"
        if releases:
            latest_fw = releases[0].version

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "active_page": "overview",
            "metrics": {
                "total_devices": total_count,
                "online_devices": online_count,
                "latest_firmware": latest_fw,
                "recent_jobs_count": len(recent_jobs),
            },
            "recent_jobs": recent_jobs,
        },
    )


@web_router.get("/devices", response_class=HTMLResponse)
async def get_devices_partial(request: Request):
    """HTMX partial for the Devices section."""
    async with PostgreSQLDatabase.get_session() as db:
        # 1. Fetch data cleanly through the Service Layer
        devices = await DeviceService.get_all(db)

        # 2. Map the domain models to frontend DTOs/Dictionaries
        now = datetime.now(timezone.utc)
        device_data = []

        for d in devices:
            is_online = False
            if d.last_seen and (now - d.last_seen) < timedelta(minutes=5):
                is_online = True

            device_data.append(
                {
                    "id": str(d.id),
                    "name": d.name,
                    "imei": d.imei,
                    "firmware": d.firmware_version or "Unknown",
                    "last_seen": d.last_seen.strftime("%b %d, %H:%M:%S")
                    if d.last_seen
                    else "Never",
                    "is_online": is_online,
                }
            )

    return templates.TemplateResponse(
        request=request,
        name="devices/main.html",
        context={"active_page": "devices", "devices": device_data},
    )


@web_router.get("/devices/{device_id}/telemetry", response_class=HTMLResponse)
async def get_device_telemetry(request: Request, device_id: str):
    """HTMX partial that returns the telemetry table for the modal."""
    async with PostgreSQLDatabase.get_session() as db:
        # Fetch the latest 50 telemetry points for this device
        telemetry_data = await TelemetryService.get_history(db, device_id, 50)

    return templates.TemplateResponse(
        request=request,
        name="devices/telemetry_table.html",
        context={"telemetry": telemetry_data},
    )


@web_router.get("/ota", response_class=HTMLResponse)
async def get_ota_partial(request: Request):
    """Main OTA Dashboard."""
    async with PostgreSQLDatabase.get_session() as db:
        devices = await DeviceService.get_all(db)
        releases = await OtaService.get_all_releases(db)
        # We don't fetch jobs here, because HTMX will load them dynamically!

    return templates.TemplateResponse(
        request=request,
        name="ota/main.html",
        context={"active_page": "ota", "devices": devices, "releases": releases},
    )


@web_router.get("/ota/jobs", response_class=HTMLResponse)
async def get_ota_jobs_table(request: Request):
    """HTMX Polling Endpoint for the Jobs Table."""
    async with PostgreSQLDatabase.get_session() as db:
        jobs = await OtaService.get_recent_jobs(db)

    return templates.TemplateResponse(
        request=request, name="ota/jobs_table.html", context={"jobs": jobs}
    )


@web_router.post("/ota/canary", response_class=HTMLResponse)
async def trigger_canary_rollout(
    request: Request, device_id: str = Form(...), release_id: str = Form(...)
):
    """Triggered via HTMX form submission to deploy to a single device."""
    async with PostgreSQLDatabase.get_session() as db:
        device = await DeviceService.get_by_id(db, device_id)
        release = await OtaService.get_release_by_id(db, release_id)
        if device and release:
            await OtaService.execute_single_rollout(db, release, device)

        # Immediately return the fresh jobs table so the UI updates
        jobs = await OtaService.get_recent_jobs(db)

    return templates.TemplateResponse(
        request=request, name="ota/jobs_table.html", context={"jobs": jobs}
    )


@web_router.post("/ota/fleet", response_class=HTMLResponse)
async def trigger_fleet_rollout(request: Request, release_id: str = Form(...)):
    """Triggered via HTMX form submission to deploy to all eligible devices."""
    async with PostgreSQLDatabase.get_session() as db:
        release = await OtaService.get_release_by_id(db, release_id)
        if release:
            await OtaService.execute_bulk_rollout(db, release)

        jobs = await OtaService.get_recent_jobs(db)

    return templates.TemplateResponse(
        request=request, name="ota/jobs_table.html", context={"jobs": jobs}
    )


@web_router.get("/trips", response_class=HTMLResponse)
async def get_trips_partial(request: Request):
    """HTMX partial for the Trips & Telemetry section."""
    async with PostgreSQLDatabase.get_session() as db:
        # Fetch the active devices to populate the dropdown
        devices = await DeviceService.get_all(db)

    return templates.TemplateResponse(
        request=request,
        name="trips/main.html",
        context={"active_page": "trips", "devices": devices},
    )
