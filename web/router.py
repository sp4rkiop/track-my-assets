from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import pathlib

from core.database import PostgreSQLDatabase
from core.deps import get_current_active_user, get_current_user
from models.user import User
from services.device_service import DeviceService
from services.ota_service import OtaService
from services.telemetry_service import TelemetryService

# Set up the Jinja2 template engine
BASE_DIR = pathlib.Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

web_router = APIRouter(prefix="/web", tags=["Frontend"])


@web_router.get("/login", response_class=HTMLResponse)
async def get_login_page(request: Request):
    """Render your login template here."""
    return templates.TemplateResponse(request=request, name="auth/login.html")


@web_router.get("/setup-password", response_class=HTMLResponse)
async def get_setup_password_page(
    request: Request,
    current_user: User = Depends(get_current_user),  # Only requires basic login
):
    # If they hit this URL manually but are already set up, bounce them to the dashboard
    if not current_user.needs_password_change:
        return RedirectResponse(url="/web/", status_code=303)

    return templates.TemplateResponse(request=request, name="auth/setup_password.html")


@web_router.get("/", response_class=HTMLResponse)
async def get_dashboard_home(
    request: Request, current_user: User = Depends(get_current_active_user)
):
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
async def get_devices_partial(
    request: Request, current_user: User = Depends(get_current_active_user)
):
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
                    "last_seen": d.last_seen.isoformat() if d.last_seen else None,
                    "is_online": is_online,
                }
            )

    return templates.TemplateResponse(
        request=request,
        name="devices/main.html",
        context={
            "active_page": "devices",
            "devices": device_data,
            "current_user": current_user,
        },
    )


@web_router.get("/devices/{device_id}/telemetry", response_class=HTMLResponse)
async def get_device_telemetry(
    request: Request,
    device_id: str,
    skip: int = 0,
    limit: int = 50,
    current_user: User = Depends(get_current_active_user),
):
    """HTMX partial that returns the telemetry table with pagination."""
    async with PostgreSQLDatabase.get_session() as db:
        telemetry_data = await TelemetryService.get_history(
            db, device_id, skip=skip, limit=limit
        )

    return templates.TemplateResponse(
        request=request,
        name="devices/telemetry_table.html",
        context={
            "telemetry": telemetry_data,
            "device_id": device_id,
            "skip": skip,
            "limit": limit,
        },
    )


@web_router.post("/devices/{device_id}/edit", response_class=HTMLResponse)
async def edit_device_name(
    request: Request,
    device_id: str,
    name: str = Form(...),
    current_user: User = Depends(get_current_active_user),
):
    """HTMX endpoint to update device name."""
    async with PostgreSQLDatabase.get_session() as db:
        await DeviceService.update_device_name(db, device_id, name)

    return await get_devices_partial(request)


@web_router.delete("/devices/{device_id}", response_class=HTMLResponse)
async def delete_device_node(
    request: Request,
    device_id: str,
    current_user: User = Depends(get_current_active_user),
):
    """HTMX endpoint to delete a device."""
    async with PostgreSQLDatabase.get_session() as db:
        await DeviceService.delete_device(db, device_id)

    return await get_devices_partial(request)


@web_router.get("/live", response_class=HTMLResponse)
async def get_live_fleet_page(
    request: Request, current_user: User = Depends(get_current_active_user)
):
    """Renders the full-screen live radar tracking view."""
    return templates.TemplateResponse(
        request=request,
        name="live/main.html",
        context={"active_page": "live", "current_user": current_user},
    )


@web_router.get("/live/data")
async def get_live_fleet_data(
    request: Request, current_user: User = Depends(get_current_active_user)
):
    """Lightweight JSON endpoint to poll live fleet coordinates."""
    async with PostgreSQLDatabase.get_session() as db:
        devices = await DeviceService.get_all(db)
        now = datetime.now(timezone.utc)
        payload = []

        for d in devices:
            # Fetch only the absolute latest payload for each device
            telemetry = await TelemetryService.get_history(
                db, str(d.id), skip=0, limit=1
            )
            is_online = bool(d.last_seen and (now - d.last_seen) < timedelta(minutes=5))

            if telemetry and telemetry[0].latitude and telemetry[0].longitude:
                payload.append(
                    {
                        "id": str(d.id),
                        "name": d.name,
                        "imei": d.imei[-6:],
                        "is_online": is_online,
                        "lat": float(telemetry[0].latitude),
                        "lng": float(telemetry[0].longitude),
                        "speed": float(telemetry[0].speed_kmh or 0),
                        "last_seen": d.last_seen.isoformat() if d.last_seen else None,
                    }
                )

    return JSONResponse(content={"fleet": payload})


@web_router.get("/ota", response_class=HTMLResponse)
async def get_ota_partial(
    request: Request, current_user: User = Depends(get_current_active_user)
):
    """Main OTA Dashboard."""
    async with PostgreSQLDatabase.get_session() as db:
        devices = await DeviceService.get_all(db)
        releases = await OtaService.get_all_releases(db)
        # We don't fetch jobs here, because HTMX will load them dynamically!

    return templates.TemplateResponse(
        request=request,
        name="ota/main.html",
        context={
            "active_page": "ota",
            "devices": devices,
            "releases": releases,
            "current_user": current_user,
        },
    )


@web_router.get("/ota/jobs", response_class=HTMLResponse)
async def get_ota_jobs_table(
    request: Request,
    skip: int = 0,
    limit: int = 20,
    current_user: User = Depends(get_current_active_user),
):
    """HTMX Polling & Pagination Endpoint for the Jobs Table."""
    async with PostgreSQLDatabase.get_session() as db:
        jobs = await OtaService.get_recent_jobs(db, skip=skip, limit=limit)

    return templates.TemplateResponse(
        request=request,
        name="ota/jobs_table.html",
        context={"jobs": jobs, "skip": skip, "limit": limit},
    )


@web_router.delete("/ota/jobs/{job_id}", response_class=HTMLResponse)
async def delete_ota_job(
    request: Request, job_id: str, current_user: User = Depends(get_current_active_user)
):
    """HTMX endpoint to delete an active or completed OTA job."""
    async with PostgreSQLDatabase.get_session() as db:
        await OtaService.delete_job(db, job_id)

    # Return an empty response. HTMX uses this to instantly remove the deleted
    # <tr> from the DOM without resetting the user's paginated list.
    return HTMLResponse("")


@web_router.post("/ota/canary", response_class=HTMLResponse)
async def trigger_canary_rollout(
    request: Request,
    device_id: str = Form(...),
    release_id: str = Form(...),
    current_user: User = Depends(get_current_active_user),
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
async def trigger_fleet_rollout(
    request: Request,
    release_id: str = Form(...),
    current_user: User = Depends(get_current_active_user),
):
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
async def get_trips_partial(
    request: Request, current_user: User = Depends(get_current_active_user)
):
    """HTMX partial for the Trips & Telemetry section."""
    async with PostgreSQLDatabase.get_session() as db:
        # Fetch the active devices to populate the dropdown
        devices = await DeviceService.get_all(db)

    return templates.TemplateResponse(
        request=request,
        name="trips/main.html",
        context={
            "active_page": "trips",
            "devices": devices,
            "current_user": current_user,
        },
    )
