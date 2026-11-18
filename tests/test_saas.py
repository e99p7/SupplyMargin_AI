from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from supplymargin.api import app
from supplymargin.database import SessionLocal
from supplymargin.job_runner import execute_run
from supplymargin.routers import billing
from supplymargin.subscriptions import activate_subscription

PASSWORD = "SecurePassword123"


def _register(client: TestClient, prefix: str) -> dict:
    email = f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"
    response = client.post(
        "/v1/auth/register",
        json={"email": email, "password": PASSWORD},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _headers(session: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {session['access_token']}"}


def test_register_refresh_and_me() -> None:
    with TestClient(app) as client:
        session = _register(client, "auth")
        me = client.get("/v1/auth/me", headers=_headers(session))
        assert me.status_code == 200
        assert me.json()["email"] == session["user"]["email"]
        refreshed = client.post("/v1/auth/refresh")
        assert refreshed.status_code == 200
        assert refreshed.json()["access_token"] != session["access_token"]
        logged_out = client.post("/v1/auth/logout")
        assert logged_out.status_code == 204
        assert client.post("/v1/auth/refresh").status_code == 401


def test_saas_upload_isolation_worker_and_download() -> None:
    demo = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "demo"
        / "retail_price_history.csv"
    ).read_bytes()
    with TestClient(app) as client:
        user_a = _register(client, "tenant-a")
        user_b = _register(client, "tenant-b")

        blocked = client.post(
            "/v1/datasets",
            headers=_headers(user_a),
            files={"file": ("history.csv", demo, "text/csv")},
        )
        assert blocked.status_code == 403

        with SessionLocal() as db:
            activate_subscription(
                db,
                user_id=user_a["user"]["id"],
                plan_code="basic",
                days=30,
                provider="test",
            )

        uploaded = client.post(
            "/v1/datasets",
            headers=_headers(user_a),
            files={"file": ("history.csv", demo, "text/csv")},
            data={"name": "Тестовая история"},
        )
        assert uploaded.status_code == 201, uploaded.text
        dataset = uploaded.json()
        version_id = dataset["latest_version"]["id"]
        assert dataset["latest_version"]["row_count"] > 20

        foreign = client.get(
            f"/v1/datasets/{dataset['id']}", headers=_headers(user_b)
        )
        assert foreign.status_code == 404

        queued = client.post(
            "/v1/runs",
            headers=_headers(user_a),
            json={
                "dataset_version_id": version_id,
                "options": {
                    "horizon": 3,
                    "min_margin": 0.15,
                    "max_price_change": 0.15,
                    "service_level": 0.9,
                    "risk_aversion": 0.25,
                    "random_seed": 42,
                },
            },
        )
        assert queued.status_code == 202, queued.text
        run_id = queued.json()["id"]

        with SessionLocal() as db:
            execute_run(db, run_id)

        completed = client.get(f"/v1/runs/{run_id}", headers=_headers(user_a))
        assert completed.status_code == 200
        assert completed.json()["status"] == "completed"
        assert completed.json()["summary"]["sku_count"] > 0

        result = client.get(f"/v1/runs/{run_id}/result", headers=_headers(user_a))
        assert result.status_code == 200
        assert result.json()["recommendations"]

        excel = client.get(f"/v1/runs/{run_id}/download", headers=_headers(user_a))
        assert excel.status_code == 200
        assert excel.content.startswith(b"PK")

        foreign_run = client.get(f"/v1/runs/{run_id}", headers=_headers(user_b))
        assert foreign_run.status_code == 404

        deleted = client.delete(
            f"/v1/datasets/{dataset['id']}", headers=_headers(user_a)
        )
        assert deleted.status_code == 204


def test_yookassa_webhook_is_verified_and_idempotent(monkeypatch) -> None:
    with TestClient(app) as client:
        session = _register(client, "payment")
        payment = {
            "id": "payment-test-001",
            "status": "succeeded",
            "paid": True,
            "amount": {"value": "990.00", "currency": "RUB"},
            "metadata": {
                "user_id": session["user"]["id"],
                "plan_code": "basic",
                "days": "9999",
            },
        }
        monkeypatch.setattr(
            billing,
            "settings",
            SimpleNamespace(yookassa_shop_id="shop", yookassa_secret_key="secret"),
        )
        monkeypatch.setattr(billing, "_payment_get", lambda _payment_id: payment)
        notification = {
            "event": "payment.succeeded",
            "object": {"id": payment["id"]},
        }

        first = client.post("/v1/billing/webhook", json=notification)
        assert first.status_code == 200, first.text
        status = client.get("/v1/billing/status", headers=_headers(session))
        assert status.status_code == 200
        first_end = status.json()["current_period_end"]
        assert status.json()["active"] is True

        repeated = client.post("/v1/billing/webhook", json=notification)
        assert repeated.status_code == 200
        assert repeated.json()["status"] == "already_processed"
        status = client.get("/v1/billing/status", headers=_headers(session))
        assert status.json()["current_period_end"] == first_end
