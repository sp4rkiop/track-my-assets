from fastapi import APIRouter, Response
from sqlalchemy import text
from core.database import PostgreSQLDatabase

router = APIRouter()


@router.get("/api/health")
async def get_system_health_beacon():
    """
    Returns an HTML fragment consumed by the dashboard sidebar.
    Verifies live connectivity to your PostgreSQL engine hypertable backend.
    """
    is_database_alive = False
    try:
        async with PostgreSQLDatabase.get_session() as db:
            await db.execute(text("SELECT 1"))
            is_database_alive = True
    except Exception:
        pass

    if is_database_alive:
        return Response(
            content="""
            <span>Server Core: Online</span>
            <div class="h-2 w-2 rounded-full bg-emerald-500 animate-pulse"></div>
            """,
            media_type="text/html",
        )

    return Response(
        content="""
        <span class="text-red-400">Database Offline</span>
        <div class="h-2 w-2 rounded-full bg-red-500 animate-ping"></div>
        """,
        media_type="text/html",
    )
