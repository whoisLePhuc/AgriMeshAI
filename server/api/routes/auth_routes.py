"""Routes: authentication."""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import create_access_token, get_current_user, hash_password, verify_password
from api.config import settings
from api.database import get_db
from api.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    role: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    role: str = "viewer"
    farm_id: int | None = None


@router.post("/login", response_model=TokenResponse)
async def login(form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)) -> TokenResponse:
    result = await db.execute(select(User).where(User.username == form_data.username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account deactivated")

    token = create_access_token({"sub": user.username, "role": user.role, "farm_id": user.farm_id})
    return TokenResponse(access_token=token, username=user.username, role=user.role)


@router.post("/register", response_model=TokenResponse)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    existing = await db.execute(select(User).where(User.username == req.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Username already taken")

    user = User(username=req.username, hashed_password=hash_password(req.password),
                role=req.role, farm_id=req.farm_id)
    db.add(user)
    await db.commit()
    await db.refresh(user)

    token = create_access_token({"sub": user.username, "role": user.role, "farm_id": user.farm_id})
    return TokenResponse(access_token=token, username=user.username, role=user.role)


@router.get("/me")
async def me(current_user: User = Depends(get_current_user)) -> dict:
    return {"username": current_user.username, "role": current_user.role, "farm_id": current_user.farm_id}
