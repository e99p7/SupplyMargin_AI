from __future__ import annotations

import argparse
import uuid

from sqlalchemy import select

from supplymargin.auth import hash_password, normalize_email
from supplymargin.database import SessionLocal, init_database
from supplymargin.models import User


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or promote a SupplyMargin admin")
    parser.add_argument("email")
    parser.add_argument("--password", help="Required only when creating a new user")
    args = parser.parse_args()
    init_database()
    email = normalize_email(args.email)
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            if not args.password or len(args.password) < 10:
                raise SystemExit("Use --password with at least 10 characters")
            user = User(
                id=str(uuid.uuid4()),
                email=email,
                password_hash=hash_password(args.password),
                is_admin=True,
            )
            db.add(user)
        else:
            user.is_admin = True
        db.commit()
    print(f"Admin ready: {email}")


if __name__ == "__main__":
    main()
