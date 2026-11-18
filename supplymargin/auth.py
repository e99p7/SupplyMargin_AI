from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from supplymargin.config import settings
from supplymargin.database import get_db
from supplymargin.models import RefreshToken, User

password_hasher = PasswordHasher()
bearer = HTTPBearer(auto_error=False)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    try:
        return password_hasher.verify(encoded, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def create_access_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.id,
        "email": user.email,
        "admin": user.is_admin,
        "type": "access",
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_refresh_token(db: Session, user: User) -> str:
    raw = secrets.token_urlsafe(48)
    db.add(
        RefreshToken(
            id=str(uuid.uuid4()),
            user_id=user.id,
            token_hash=token_hash(raw),
            expires_at=datetime.now(timezone.utc)
            + timedelta(days=settings.refresh_token_days),
        )
    )
    db.commit()
    return raw


def rotate_refresh_token(db: Session, raw: str) -> tuple[User, str]:
    record = db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash(raw))
    )
    now = datetime.now(timezone.utc)
    if record is None or record.revoked_at is not None or _utc(record.expires_at) <= now:
        raise HTTPException(status_code=401, detail="Сессия истекла. Войдите снова.")
    user = db.get(User, record.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Пользователь недоступен.")
    record.revoked_at = now
    db.commit()
    return user, issue_refresh_token(db, user)


def revoke_refresh_token(db: Session, raw: str | None) -> None:
    if not raw:
        return
    record = db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash(raw))
    )
    if record is not None and record.revoked_at is None:
        record.revoked_at = datetime.now(timezone.utc)
        db.commit()


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(status_code=401, detail="Требуется авторизация.")
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_secret,
            algorithms=["HS256"],
        )
        if payload.get("type") != "access":
            raise jwt.InvalidTokenError()
        user_id = str(payload["sub"])
    except (jwt.InvalidTokenError, KeyError) as exc:
        raise HTTPException(status_code=401, detail="Недействительный токен.") from exc
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Пользователь недоступен.")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Требуются права администратора.")
    return user


def serialize_user(user: User) -> dict:
    created_at = _utc(user.created_at).isoformat()
    return {
        "id": user.id,
        "email": user.email,
        "is_admin": user.is_admin,
        "created_at": created_at,
    }
