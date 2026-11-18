from sqlalchemy import select
from supplymargin.database import SessionLocal
from supplymargin.models import User
from supplymargin.subscriptions import activate_subscription, subscription_payload

email = "test@test.com"

with SessionLocal() as db:
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        raise SystemExit("STOP: account not found.")

    current = subscription_payload(
        db.scalar(
            select(__import__("supplymargin.models", fromlist=["Subscription"]).Subscription)
            .where(__import__("supplymargin.models", fromlist=["Subscription"]).Subscription.user_id == user.id)
        )
    )
    print("Account:", user.email)
    print("Current subscription:", current)

    confirm = input("Для активации введи YES в PowerShell: ")
    if confirm.strip() != "YES":
        raise SystemExit("Cancelled; database unchanged.")

    sub = activate_subscription(
        db,
        user_id=user.id,
        plan_code="basic",
        days=30,
        provider="manual-test",
    )
    sub.status = "trial"
    db.commit()
    db.refresh(sub)

    print("Активировано:", user.email)
    print("Статус:", sub.status)
    print("Тариф:", sub.plan_code)
    print("Действует до:", sub.current_period_end)
