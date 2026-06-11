from fastapi import APIRouter, Depends, Form, HTTPException, status, Response
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from core.deps import get_current_user, get_db
from core.security import get_password_hash, verify_password, create_access_token
from models.user import User

router = APIRouter()


@router.post("/login")
async def login(
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.email == form_data.username))
    user = result.scalar_one_or_none()

    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect email or password")

    access_token = create_access_token(data={"sub": str(user.id)})

    response.set_cookie(
        key="access_token",
        value=f"Bearer {access_token}",
        httponly=True,
        secure=False,  # Set True for production HTTPS
        samesite="lax",
        max_age=14 * 24 * 60 * 60,
    )

    # Instruct HTMX to redirect the browser natively based on user state
    redirect_url = "/web/setup-password" if user.needs_password_change else "/web/"
    response.headers["HX-Redirect"] = redirect_url

    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/change-password")
async def change_password(
    response: Response,
    password: str = Form(...),
    confirm_password: str = Form(...),
    current_user: User = Depends(
        get_current_user
    ),  # Allowed even if needs_password_change=True
    db: AsyncSession = Depends(get_db),
):
    if password != confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match")

    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Password too short")

    current_user.hashed_password = get_password_hash(password)
    current_user.needs_password_change = False

    db.add(current_user)
    await db.commit()

    # Tell HTMX to push the user to the dashboard now that they are set up
    response.headers["HX-Redirect"] = "/web/"
    return {"status": "success"}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("access_token")
    return {"message": "Logged out successfully"}
