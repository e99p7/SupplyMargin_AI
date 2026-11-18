from __future__ import annotations

from supplymargin.data import normalize_history
from supplymargin.quality import analyze_history_quality
from supplymargin.schemas import OptimizeOptions
from supplymargin.service import (
    build_shadow_report,
    register_outcomes,
    run_optimization,
)
from supplymargin.storage import RunStore
from tests.test_pipeline import synthetic_history


def test_history_quality_is_per_sku() -> None:
    normalized = normalize_history(synthetic_history())
    quality = analyze_history_quality(normalized.frame)
    assert quality["sku_count"] == 3
    assert len(quality["per_sku"]) == 3
    assert all(row["observations"] == 18 for row in quality["per_sku"])
    assert all(row["eligible_history"] for row in quality["per_sku"])


def test_shadow_outcomes_round_trip(tmp_path, market_snapshot: dict) -> None:
    store = RunStore(tmp_path)
    result = run_optimization(
        normalized=normalize_history(synthetic_history()),
        market=market_snapshot,
        options=OptimizeOptions(horizon=3),
        store=store,
    )
    run_id = result["summary"]["run_id"]
    rec = result["recommendations"][0]
    saved = register_outcomes(
        store=store,
        outcomes=[
            {
                "run_id": run_id,
                "sku": rec["sku"],
                "actual_date": "2026-08-01",
                "actual_sell_price": rec["current_sell_price"],
                "actual_units": rec["forecast_units"],
                "actual_purchase_cost": rec["current_purchase_price"],
                "actual_shipping_cost": 4.0,
                "stock_end": 10.0,
            }
        ],
    )
    assert saved["saved"] == 1
    report = build_shadow_report(store=store, run_id=run_id)
    assert report["status"] == "shadow_observed"
    assert report["observations"] == 1
    assert report["causal_uplift_claim"] is False
