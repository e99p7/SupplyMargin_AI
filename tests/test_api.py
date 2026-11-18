from __future__ import annotations

from fastapi.testclient import TestClient

from supplymargin.api import app
from tests.test_integration import operational_tables


def test_health_and_market() -> None:
    client = TestClient(app)
    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["mode"] == "shadow"
    market = client.get("/v1/market")
    assert market.status_code == 200
    assert market.json()["as_of"] == "2026-07-25"
    mode = client.get("/v1/system/mode")
    assert mode.status_code == 200
    assert mode.json()["mode"] == "shadow"
    assert mode.json()["publication_adapter_configured"] is False


def test_dashboard() -> None:
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "SupplyMargin AI" in response.text
    assert "shadow" in response.text


def test_shadow_mode_blocks_decisions() -> None:
    client = TestClient(app)
    response = client.post(
        "/v1/decisions",
        json={
            "run_id": "run_missing",
            "sku": "A",
            "decision": "approve",
            "reason": "test decision",
        },
    )
    assert response.status_code == 409


def test_operational_upload_endpoint() -> None:
    sales, purchases, inventory, logistics = operational_tables()
    client = TestClient(app)
    response = client.post(
        "/v1/optimize/operational-upload",
        files={
            "sales": ("sales.csv", sales.to_csv(index=False), "text/csv"),
            "purchase_costs": (
                "purchase_costs.csv",
                purchases.to_csv(index=False),
                "text/csv",
            ),
            "daily_stock": (
                "daily_stock.csv",
                inventory.to_csv(index=False),
                "text/csv",
            ),
            "logistics_costs": (
                "logistics_costs.csv",
                logistics.to_csv(index=False),
                "text/csv",
            ),
        },
        data={"horizon": "3"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["integration_report"]["actual_coverage"]["complete_logistics"] == 1
    assert body["summary"]["production_data_ready_skus"] == 3
    data_reasons = {
        "purchase_cost_not_actual",
        "daily_inventory_not_actual",
        "logistics_cost_not_actual",
    }
    assert all(
        not data_reasons.intersection(row["approval_gate_reasons"])
        for row in body["recommendations"]
    )
