import jwt
from datetime import datetime, timedelta, timezone
import bcrypt
from sqlalchemy import select
from core.config import settings
from core.database import PostgreSQLDatabase
from models.user import User


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plain password against the stored bcrypt hash."""
    # bcrypt requires bytes, so we encode the strings
    password_bytes = plain_password.encode("utf-8")
    hash_bytes = hashed_password.encode("utf-8")

    return bcrypt.checkpw(password_bytes, hash_bytes)


def get_password_hash(password: str) -> str:
    """Hashes a password using bcrypt."""
    password_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt()

    hashed_password = bcrypt.hashpw(password_bytes, salt)

    # Decode back to string to store in the database (SQLAlchemy String column)
    return hashed_password.decode("utf-8")


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


async def seed_default_admin():
    """Checks for existing users; if none, creates the default NPM-style admin."""
    async with PostgreSQLDatabase.get_session() as db:
        result = await db.execute(select(User).limit(1))
        if not result.scalar_one_or_none():
            from core.config import settings

            admin = User(
                email=settings.DEFAULT_ADMIN_EMAIL,
                hashed_password=get_password_hash(settings.DEFAULT_ADMIN_PASSWORD),
                needs_password_change=True,
            )
            db.add(admin)
