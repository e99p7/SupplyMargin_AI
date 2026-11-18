from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from supplymargin.auth import normalize_email
from supplymargin.config import settings
from supplymargin.database import get_db
from supplymargin.models import User
from supplymargin.schemas import AdminSubscriptionRequest
from supplymargin.subscriptions import activate_subscription, subscription_payload

router = APIRouter(prefix="/v1/admin", tags=["admin"])


def require_admin_key(
    x_admin_key: Annotated[str | None, Header(alias="X-Admin-Key")] = None,
) -> None:
    if not settings.admin_api_key or x_admin_key != settings.admin_api_key:
        raise HTTPException(status_code=404, detail="Not found.")


@router.post(
    "/subscriptions/{email}",
    dependencies=[Depends(require_admin_key)],
)
def grant_subscription(
    email: str,
    request: AdminSubscriptionRequest,
    db: Session = Depends(get_db),
) -> dict:
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден.")
    subscription = activate_subscription(
        db,
        user_id=user.id,
        plan_code=request.plan_code,
        days=request.days,
        provider="admin",
    )
    return {"user_id": user.id, **subscription_payload(subscription)}
