from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
import jwt
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from core.database import PostgreSQLDatabase
from core.config import settings
from models.user import User


class WebAuthException(Exception):
    pass


class RequiresPasswordChangeException(Exception):
    """Raised when a user logs in but still has needs_password_change=True"""

    pass


async def get_db():
    async with PostgreSQLDatabase.get_session() as session:
        yield session


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


async def get_token_from_request(request: Request, token: str = Depends(oauth2_scheme)):
    # 1. Check Authorization header first
    if token:
        return token
    # 2. Fallback to HttpOnly cookie
    cookie_token = request.cookies.get("access_token")
    if cookie_token and cookie_token.startswith("Bearer "):
        return cookie_token.split(" ")[1]
    return None


async def get_current_user(
    request: Request,
    token: str = Depends(get_token_from_request),
    db: AsyncSession = Depends(get_db),
):
    is_web = request.url.path.startswith("/web")

    if not token:
        if is_web:
            raise WebAuthException()
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        user_id: str = payload.get("sub", "na")
        if user_id is None:
            raise ValueError()
    except (jwt.PyJWTError, ValueError):
        if is_web:
            raise WebAuthException()
        raise HTTPException(status_code=401, detail="Invalid token")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        if is_web:
            raise WebAuthException()
        raise HTTPException(status_code=401, detail="User not found")

    return user


async def get_current_active_user(
    request: Request, current_user: User = Depends(get_current_user)
):
    """Protects standard routes. Blocks users who need to change their password."""
    is_web = request.url.path.startswith("/web")

    if current_user.needs_password_change:
        if is_web:
            raise RequiresPasswordChangeException()
        raise HTTPException(status_code=403, detail="Password change required")

    return current_user
