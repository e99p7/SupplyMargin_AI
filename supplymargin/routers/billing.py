from __future__ import annotations

import json
import uuid
from decimal import Decimal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from supplymargin.auth import get_current_user
from supplymargin.config import settings
from supplymargin.database import get_db
from supplymargin.models import PaymentEvent, User
from supplymargin.plans import all_plans, get_plan
from supplymargin.schemas import CheckoutRequest
from supplymargin.subscriptions import (
    activate_subscription,
    get_subscription,
    subscription_payload,
)

router = APIRouter(prefix="/v1/billing", tags=["billing"])


def _configured() -> bool:
    return bool(settings.yookassa_shop_id and settings.yookassa_secret_key)


def _payment_get(payment_id: str) -> dict:
    with httpx.Client(timeout=20) as client:
        response = client.get(
            f"https://api.yookassa.ru/v3/payments/{payment_id}",
            auth=(settings.yookassa_shop_id, settings.yookassa_secret_key),
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="Не удалось проверить платеж.")
    return response.json()


@router.get("/plans")
def plans() -> list[dict]:
    return [plan.public() for plan in all_plans().values()]


@router.get("/status")
def billing_status(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return subscription_payload(get_subscription(db, user.id))


@router.post("/checkout")
def checkout(
    body: CheckoutRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if not _configured():
        raise HTTPException(status_code=503, detail="ЮKassa пока не настроена.")
    plan = get_plan(body.plan_code)
    idempotency_key = str(uuid.uuid4())
    payload = {
        "amount": {"value": f"{plan.price_rub:.2f}", "currency": "RUB"},
        "capture": True,
        "confirmation": {
            "type": "redirect",
            "return_url": settings.yookassa_return_url,
        },
        "description": f"SupplyMargin AI — тариф {plan.name_ru}, 30 дней",
        "save_payment_method": False,
        "metadata": {"user_id": user.id, "plan_code": plan.code, "days": "30"},
    }
    with httpx.Client(timeout=20) as client:
        response = client.post(
            "https://api.yookassa.ru/v3/payments",
            auth=(settings.yookassa_shop_id, settings.yookassa_secret_key),
            headers={"Idempotence-Key": idempotency_key},
            json=payload,
        )
    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail="ЮKassa не создала платеж. Проверьте настройки магазина.",
        )
    payment = response.json()
    db.add(
        PaymentEvent(
            id=str(uuid.uuid4()),
            event_id=f"created:{payment['id']}",
            payment_id=payment["id"],
            user_id=user.id,
            status=payment.get("status", "pending"),
            amount_value=payment["amount"]["value"],
            payload_json=payment,
        )
    )
    db.commit()
    confirmation_url = payment.get("confirmation", {}).get("confirmation_url")
    if not confirmation_url:
        raise HTTPException(status_code=502, detail="ЮKassa не вернула ссылку на оплату.")
    return {"payment_id": payment["id"], "confirmation_url": confirmation_url}


@router.post("/webhook", include_in_schema=False)
async def yookassa_webhook(request: Request, db: Session = Depends(get_db)) -> dict:
    if not _configured():
        raise HTTPException(status_code=503, detail="ЮKassa не настроена.")
    try:
        notification = await request.json()
        event = str(notification["event"])
        payment_id = str(notification["object"]["id"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Некорректное уведомление.") from exc

    verified = _payment_get(payment_id)
    status = str(verified.get("status", "unknown"))
    event_id = f"{event}:{payment_id}:{status}"
    if db.scalar(select(PaymentEvent).where(PaymentEvent.event_id == event_id)):
        return {"status": "already_processed"}

    metadata = verified.get("metadata") or {}
    user_id = str(metadata.get("user_id", ""))
    user = db.get(User, user_id) if user_id else None
    amount_data = verified.get("amount") or {}
    amount = str(amount_data.get("value", "0"))
    currency = str(amount_data.get("currency", ""))
    db.add(
        PaymentEvent(
            id=str(uuid.uuid4()),
            event_id=event_id,
            payment_id=payment_id,
            user_id=user.id if user else None,
            status=status,
            amount_value=amount,
            payload_json=verified,
        )
    )

    if (
        event == "payment.succeeded"
        and status == "succeeded"
        and verified.get("paid") is True
        and user is not None
    ):
        plan_code = str(metadata.get("plan_code", "basic"))
        try:
            plan = get_plan(plan_code)
            amount_decimal = Decimal(amount)
        except (ValueError, TypeError, ArithmeticError) as exc:
            db.rollback()
            raise HTTPException(status_code=400, detail="Некорректные данные платежа.") from exc
        if currency != "RUB" or amount_decimal != Decimal(plan.price_rub).quantize(
            Decimal("0.01")
        ):
            db.rollback()
            raise HTTPException(status_code=400, detail="Сумма или валюта платежа не совпадает.")
        payment_method = verified.get("payment_method") or {}
        activate_subscription(
            db,
            user_id=user.id,
            plan_code=plan.code,
            days=30,
            provider="yookassa",
            payment_method_id=(payment_method.get("id") if payment_method.get("saved") else None),
        )
    else:
        db.commit()
    return {"status": "ok"}
