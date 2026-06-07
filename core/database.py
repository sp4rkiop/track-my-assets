import subprocess
import sys
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker
from contextlib import asynccontextmanager
from core.config import settings
from typing import AsyncGenerator, Any
from urllib.parse import quote_plus
from core.logger import get_logger


# Configure logging
logger = get_logger(__name__)

# Async database engine and session factory
DATABASE_URL = f"postgresql+asyncpg://{quote_plus(settings.DB_USER)}:{quote_plus(settings.DB_PASSWORD)}@{settings.DB_HOST}:{settings.DB_PORT}/{quote_plus(settings.DB_NAME)}"
engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
    class_=AsyncSession,
)


class PostgreSQLDatabase:
    @classmethod
    async def initialize(cls) -> None:
        """
        Initialize the database by creating tables if they don't exist.
        Uses async connection and reflects table creation status.
        """
        try:
            async with engine.begin() as conn:
                # Enable the extension (requires superuser; already present on
                #    TimescaleDB images but harmless to repeat).
                await conn.execute(
                    text("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;")
                )
                # Enable pgcrypto for UUID generation
                await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto";'))
                # Create all tables defined in the ORM models
                # await conn.run_sync(Base.metadata.create_all)

                # Runs 'alembic upgrade head' automatically
                subprocess.run(
                    [sys.executable, "-m", "alembic", "upgrade", "head"], check=True
                )
            logger.info("Database connection initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    @classmethod
    @asynccontextmanager
    async def get_session(cls) -> AsyncGenerator[AsyncSession, Any]:
        """
        Async context manager for database sessions.
        Ensures proper transaction handling and resource cleanup.
        """
        async with AsyncSessionLocal() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                logger.error("Transaction rolled back due to an error")
                raise

    @classmethod
    async def close_all_connections(cls) -> None:
        """
        Close all database connections and dispose connection pool.
        """
        await engine.dispose()
        logger.info("Database connection closed and connection pool disposed.")
