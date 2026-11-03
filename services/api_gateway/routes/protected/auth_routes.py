from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, Field, root_validator, validator
from sqlalchemy.orm import Session

from config.database_config import get_db
from config.logging_config import get_logger
from database.models import (
    User,
)
from services.api_gateway.auth import (
    authenticate_user,
    create_token_pair,
    create_user,
    get_current_user,
    get_password_hash,
    validate_password,
    verify_password,
    verify_refresh_token,
)

from services.api_gateway.routes.protected.common import (
    _serialize_user,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["Protected"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8)
    name: Optional[str] = None
    full_name: Optional[str] = None

    @root_validator(pre=True)
    def normalize_name(cls, values):
        if not values.get("name") and values.get("full_name"):
            values["name"] = values["full_name"]
        if not values.get("full_name") and values.get("name"):
            values["full_name"] = values["name"]
        return values

    @validator("password")
    def validate_password_value(cls, value: str) -> str:
        return validate_password(value)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class UpdateProfileRequest(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    company: Optional[str] = None


class ChangePasswordRequest(BaseModel):
    currentPassword: str
    newPassword: str = Field(..., min_length=8)

    @validator("newPassword")
    def validate_new_password(cls, value: str) -> str:
        return validate_password(value)


@router.post("/auth/register")
async def register(request: RegisterRequest, db: Session = Depends(get_db)):
    existing_user = db.query(User).filter(User.email == request.email).first()
    if existing_user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

    try:
        user = create_user(
            db=db,
            email=request.email,
            password=request.password,
            full_name=request.full_name or request.name or "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    tokens = create_token_pair(user_id=str(user.id), email=user.email)

    return {
        "success": True,
        "user": _serialize_user(user),
        "token": tokens.access_token,
        "refreshToken": tokens.refresh_token,
        "tokens": tokens.dict(),
    }


@router.post("/auth/login")
async def login(request: LoginRequest, db: Session = Depends(get_db)):
    user = authenticate_user(db, request.email, request.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")

    tokens = create_token_pair(user_id=str(user.id), email=user.email)

    return {
        "success": True,
        "user": _serialize_user(user),
        "token": tokens.access_token,
        "refreshToken": tokens.refresh_token,
        "tokens": tokens.dict(),
    }


@router.post("/auth/refresh")
async def refresh_token(request: RefreshTokenRequest, db: Session = Depends(get_db)):
    token_data = verify_refresh_token(request.refresh_token)
    user = db.query(User).filter(User.id == token_data.user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    tokens = create_token_pair(user_id=str(user.id), email=user.email)
    return {"token": tokens.access_token, "refreshToken": tokens.refresh_token, "tokens": tokens.dict()}


@router.get("/auth/me")
@router.get("/me")
async def get_current_user_info(current_user: User = Depends(get_current_user)):
    return _serialize_user(current_user)


@router.patch("/auth/profile")
async def update_profile(
    request: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if request.email:
        email_owner = db.query(User).filter(User.email == request.email, User.id != current_user.id).first()
        if email_owner:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already in use")
        current_user.email = request.email

    if request.name is not None:
        current_user.full_name = request.name

    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    return _serialize_user(current_user)


@router.post("/auth/change-password")
async def change_password(
    request: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(request.currentPassword, current_user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    if request.currentPassword == request.newPassword:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="New password must differ from the current password")

    current_user.hashed_password = get_password_hash(request.newPassword)
    db.add(current_user)
    db.commit()
    return {"success": True}


@router.post("/auth/logout")
async def logout(_: User = Depends(get_current_user)):
    return {"success": True}
