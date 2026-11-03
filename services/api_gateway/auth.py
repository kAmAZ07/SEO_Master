import os
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config.database_config import get_db
from config.logging_config import get_logger
from database.models import User
from services.api_gateway.config import settings
from services.api_gateway.session_store import (
    SessionStoreUnavailable,
    get_session_store,
    store_unavailable_error,
)

logger = get_logger(__name__)

MIN_PASSWORD_LENGTH = 8
ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"
# Claims, без которых токен не принимается ни при каких условиях.
REQUIRED_CLAIMS = ("sub", "email", "type", "jti", "sid", "iat", "exp", "iss", "aud")

pwd_context = CryptContext(schemes=["pbkdf2_sha256", "bcrypt"], deprecated="auto")
security = HTTPBearer()


class TokenData(BaseModel):
    user_id: str
    email: str
    exp: datetime
    token_type: str = ACCESS_TOKEN_TYPE
    session_id: Optional[str] = None
    jti: Optional[str] = None
    issued_at: Optional[float] = None


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


def validate_password(password: str) -> str:
    if password is None:
        raise ValueError("Password is required")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must contain at least {MIN_PASSWORD_LENGTH} characters")
    return password


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except Exception:
        return False


def get_password_hash(password: str) -> str:
    validate_password(password)
    return pwd_context.hash(password)


def _session_ttl_seconds() -> int:
    """Срок, на который помнят отзыв: не меньше максимального срока жизни refresh-токена."""
    return int(settings.REFRESH_TOKEN_EXPIRE_DAYS) * 86400


def _encode(data: dict, token_type: str, lifetime_seconds: int) -> str:
    now = time.time()
    claims = dict(data)
    claims.update(
        {
            "type": token_type,
            "iat": now,
            "exp": int(now) + int(lifetime_seconds),
            "jti": uuid.uuid4().hex,
            "iss": settings.JWT_ISSUER,
            "aud": settings.JWT_AUDIENCE,
        }
    )
    claims.setdefault("sid", uuid.uuid4().hex)
    return jwt.encode(claims, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_access_token(data: dict, expires_delta=None) -> str:
    lifetime = int(expires_delta.total_seconds()) if expires_delta else settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    return _encode(data, ACCESS_TOKEN_TYPE, lifetime)


def create_refresh_token(data: dict) -> str:
    return _encode(data, REFRESH_TOKEN_TYPE, settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400)


def create_token_pair(user_id: str, email: str, session_id: Optional[str] = None) -> TokenPair:
    """Выпускает пару токенов. Новая сессия получает новый `sid`, при ротации `sid` сохраняется."""
    data = {"sub": user_id, "email": email, "sid": session_id or uuid.uuid4().hex}
    return TokenPair(
        access_token=create_access_token(data),
        refresh_token=create_refresh_token(data),
    )


def _unauthorized(detail: str = "Could not validate credentials") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _decode(token: str, expected_type: str, invalid_detail: str) -> TokenData:
    """Проверяет подпись, срок, издателя, аудиторию, тип, обязательные claims и состояние сессии."""
    try:
        claims = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE,
            issuer=settings.JWT_ISSUER,
            options={f"require_{name}": True for name in ("exp", "iat", "sub", "jti", "iss", "aud")},
        )
    except JWTError as exc:
        logger.info("Token rejected", extra={"reason": type(exc).__name__})
        raise _unauthorized(invalid_detail) from exc

    missing = [name for name in REQUIRED_CLAIMS if not claims.get(name)]
    if missing:
        logger.info("Token rejected", extra={"reason": "missing_claims", "claims": missing})
        raise _unauthorized(invalid_detail)

    if claims["type"] != expected_type:
        logger.info("Token rejected", extra={"reason": "wrong_type", "expected": expected_type})
        raise _unauthorized(invalid_detail)

    try:
        issued_at = float(claims["iat"])
        expires_at = datetime.fromtimestamp(int(claims["exp"]), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError) as exc:
        raise _unauthorized(invalid_detail) from exc

    user_id = str(claims["sub"])
    session_id = str(claims["sid"])
    try:
        store = get_session_store()
        if store.is_session_revoked(session_id):
            logger.info("Token rejected", extra={"reason": "session_revoked"})
            raise _unauthorized(invalid_detail)
        revoked_before = store.user_revoked_before(user_id)
    except SessionStoreUnavailable as exc:
        raise store_unavailable_error() from exc
    if revoked_before is not None and issued_at <= revoked_before:
        logger.info("Token rejected", extra={"reason": "issued_before_revocation"})
        raise _unauthorized(invalid_detail)

    return TokenData(
        user_id=user_id,
        email=str(claims["email"]),
        exp=expires_at,
        token_type=expected_type,
        session_id=session_id,
        jti=str(claims["jti"]),
        issued_at=issued_at,
    )


def decode_token(token: str) -> TokenData:
    """Разбирает access-токен. Refresh-токен здесь не принимается."""
    return _decode(token, ACCESS_TOKEN_TYPE, "Could not validate credentials")


def verify_refresh_token(token: str) -> TokenData:
    """Разбирает refresh-токен. Access-токен здесь не принимается."""
    return _decode(token, REFRESH_TOKEN_TYPE, "Could not validate refresh token")


def rotate_refresh_token(token: str) -> tuple[TokenPair, TokenData]:
    """Обменивает refresh-токен на новую пару и запрещает повторное использование старого.

    Если уже обменянный токен предъявлен ещё раз, считаем его украденным: вся сессия
    отзывается, и клиент должен войти заново. Возвращает новую пару и данные старого токена.
    """
    data = verify_refresh_token(token)
    ttl = max(1, int((data.exp - datetime.now(timezone.utc)).total_seconds()))
    try:
        first_use = get_session_store().mark_refresh_used(data.jti, ttl)
        if not first_use:
            get_session_store().revoke_session(data.session_id, _session_ttl_seconds())
            logger.warning("Refresh token reuse detected", extra={"session_id": data.session_id})
            raise _unauthorized("Could not validate refresh token")
    except SessionStoreUnavailable as exc:
        raise store_unavailable_error() from exc
    return create_token_pair(user_id=data.user_id, email=data.email, session_id=data.session_id), data


def revoke_session(token_data: TokenData) -> None:
    """Выход из аккаунта: закрывает текущую сессию вместе с её refresh-токеном."""
    try:
        get_session_store().revoke_session(token_data.session_id, _session_ttl_seconds())
    except SessionStoreUnavailable as exc:
        raise store_unavailable_error() from exc


def revoke_all_sessions(user_id: str) -> None:
    """Закрывает все выпущенные ранее токены пользователя (например, после смены пароля)."""
    try:
        get_session_store().revoke_user_before(user_id, time.time(), _session_ttl_seconds())
    except SessionStoreUnavailable as exc:
        raise store_unavailable_error() from exc


async def get_current_token_data(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> TokenData:
    return decode_token(credentials.credentials)


async def get_current_user(
    token_data: TokenData = Depends(get_current_token_data),
    db: Session = Depends(get_db),
) -> User:
    user = db.query(User).filter(User.id == token_data.user_id).first()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user"
        )

    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user)
) -> User:
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Inactive user"
        )
    return current_user


def authenticate_user(db: Session, email: str, password: str) -> Optional[User]:
    user = db.query(User).filter(User.email == email).first()

    if not user:
        return None

    if not verify_password(password, user.hashed_password):
        return None

    return user


def create_user(db: Session, email: str, password: str, full_name: str) -> User:
    hashed_password = get_password_hash(password)

    user = User(
        email=email,
        hashed_password=hashed_password,
        full_name=full_name,
        is_active=True
    )

    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("Email already registered") from exc
    db.refresh(user)

    return user
