from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from supplymargin.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    issue_refresh_token,
    normalize_email,
    revoke_refresh_token,
    rotate_refresh_token,
    serialize_user,
    verify_password,
)
from supplymargin.config import settings
from supplymargin.database import get_db
from supplymargin.models import RefreshToken, Subscription, User
from supplymargin.object_storage import storage
from supplymargin.schemas import (
    ChangePasswordRequest,
    DeleteAccountRequest,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)

router = APIRouter(prefix="/v1/auth", tags=["auth"])
REFRESH_COOKIE = "supplymargin_refresh"


def _set_refresh_cookie(response: Response, value: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=value,
        max_age=settings.refresh_token_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/v1/auth",
    )


def _token_response(user: User) -> dict:
    return {
        "access_token": create_access_token(user),
        "token_type": "bearer",
        "expires_in": settings.access_token_minutes * 60,
        "user": serialize_user(user),
    }


def _validate_password(password: str) -> None:
    # Минимальная длина проверяется также в Pydantic-схеме.
    # Разрешаем любые символы: буква и цифра не обязательны.
    if len(password) < 7:
        raise HTTPException(
            status_code=422,
            detail="Пароль должен содержать не менее 7 символов.",
        )


@router.post("/register", response_model=TokenResponse, status_code=201)
def register(
    request: RegisterRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    email = normalize_email(str(request.email))
    _validate_password(request.password)
    if db.scalar(select(User).where(User.email == email)) is not None:
        raise HTTPException(status_code=409, detail="Пользователь уже зарегистрирован.")
    user = User(
        id=str(uuid.uuid4()),
        email=email,
        password_hash=hash_password(request.password),
    )
    db.add(user)
    db.flush()
    if settings.trial_days:
        now = datetime.now(timezone.utc)
        db.add(
            Subscription(
                id=str(uuid.uuid4()),
                user_id=user.id,
                plan_code="basic",
                status="trial",
                provider="trial",
                current_period_start=now,
                current_period_end=now + timedelta(days=settings.trial_days),
            )
        )
    db.commit()
    db.refresh(user)
    _set_refresh_cookie(response, issue_refresh_token(db, user))
    return _token_response(user)


@router.post("/login", response_model=TokenResponse)
def login(
    request: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    user = db.scalar(select(User).where(User.email == normalize_email(str(request.email))))
    if user is None or not verify_password(request.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Неверная почта или пароль.")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Аккаунт отключен.")
    _set_refresh_cookie(response, issue_refresh_token(db, user))
    return _token_response(user)


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    response: Response,
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
    db: Session = Depends(get_db),
) -> dict:
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Сессия отсутствует.")
    user, new_token = rotate_refresh_token(db, refresh_token)
    _set_refresh_cookie(response, new_token)
    return _token_response(user)


@router.post("/logout", status_code=204)
def logout(
    response: Response,
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
    db: Session = Depends(get_db),
) -> Response:
    revoke_refresh_token(db, refresh_token)
    response.delete_cookie(REFRESH_COOKIE, path="/v1/auth")
    response.status_code = 204
    return response


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(get_current_user)) -> dict:
    return serialize_user(user)


@router.post("/change-password", status_code=204)
def change_password(
    body: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="Текущий пароль указан неверно.")
    _validate_password(body.new_password)
    user.password_hash = hash_password(body.new_password)
    db.query(RefreshToken).filter(RefreshToken.user_id == user.id).delete()
    db.commit()
    return Response(status_code=204)


@router.delete("/account", status_code=204)
def delete_account(
    body: DeleteAccountRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Пароль указан неверно.")
    user_id = user.id
    db.delete(user)
    db.commit()
    storage.delete_prefix(f"users/{user_id}")
    return Response(status_code=204)
