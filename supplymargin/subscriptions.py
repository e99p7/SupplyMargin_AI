from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from supplymargin.auth import get_current_user
from supplymargin.database import get_db
from supplymargin.models import Subscription, User
from supplymargin.plans import get_plan


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def get_subscription(db: Session, user_id: str) -> Subscription | None:
    return db.scalar(select(Subscription).where(Subscription.user_id == user_id))


def subscription_is_active(subscription: Subscription | None) -> bool:
    if subscription is None or subscription.status not in {"active", "trial"}:
        return False
    period_end = as_utc(subscription.current_period_end)
    return period_end is not None and period_end > datetime.now(timezone.utc)


def activate_subscription(
    db: Session,
    *,
    user_id: str,
    plan_code: str,
    days: int = 30,
    provider: str = "manual",
    payment_method_id: str | None = None,
) -> Subscription:
    get_plan(plan_code)
    now = datetime.now(timezone.utc)
    subscription = get_subscription(db, user_id)
    if subscription is None:
        subscription = Subscription(id=str(uuid.uuid4()), user_id=user_id)
        db.add(subscription)
    current_end = as_utc(subscription.current_period_end)
    start = current_end if current_end and current_end > now else now
    subscription.plan_code = plan_code
    subscription.status = "active"
    subscription.provider = provider
    subscription.current_period_start = now
    subscription.current_period_end = start + timedelta(days=days)
    subscription.provider_payment_method_id = payment_method_id
    subscription.cancel_at_period_end = False
    db.commit()
    db.refresh(subscription)
    return subscription


def subscription_payload(subscription: Subscription | None) -> dict:
    if subscription is None:
        return {
            "status": "inactive",
            "active": False,
            "plan_code": None,
            "current_period_end": None,
            "cancel_at_period_end": False,
        }
    return {
        "status": subscription.status,
        "active": subscription_is_active(subscription),
        "plan_code": subscription.plan_code,
        "current_period_end": (
            as_utc(subscription.current_period_end).isoformat()
            if subscription.current_period_end
            else None
        ),
        "cancel_at_period_end": subscription.cancel_at_period_end,
    }


def require_active_subscription(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    if not subscription_is_active(get_subscription(db, user.id)):
        raise HTTPException(
            status_code=403,
            detail="Для загрузки и оптимизации нужна активная подписка.",
        )
    return user
