from __future__ import annotations

import numpy as np
import pandas as pd

from supplymargin.data import normalize_history
from supplymargin.schemas import OptimizeOptions
from supplymargin.service import run_optimization


def synthetic_history() -> pd.DataFrame:
    rows = []
    rng = np.random.default_rng(7)
    dates = pd.date_range("2025-01-01", periods=18, freq="MS")
    for sku_idx, sku in enumerate(["A", "B", "C"]):
        for step, current_date in enumerate(dates):
            price = 100 + sku_idx * 35 + (step % 4) * 3
            units = max(
                1,
                40
                + sku_idx * 8
                + 6 * np.sin(step / 2)
                - 0.35 * (price - (100 + sku_idx * 35))
                + rng.normal(0, 2),
            )
            rows.append(
                {
                    "sku": sku,
                    "date": current_date,
                    "units": units,
                    "sell_price": price,
                    "purchase_cost": price * 0.55,
                    "shipping_cost": 4,
                    "handling_cost": 1,
                    "storage_cost_per_unit_day": 0.05,
                    "stock": 25,
                    "on_order": 0,
                    "category": "cat1" if sku != "C" else "cat2",
                    "competitor_price": price * 1.02,
                    "lead_time_days": 10,
                }
            )
    return pd.DataFrame(rows)


def test_full_pipeline(market_snapshot: dict) -> None:
    result = run_optimization(
        normalized=normalize_history(synthetic_history()),
        market=market_snapshot,
        options=OptimizeOptions(horizon=3),
    )
    assert result["summary"]["sku_count"] == 3
    assert result["summary"]["execution_mode"] == "shadow"
    assert result["summary"]["history_quality_grade"] in {"A", "B", "C", "D"}
    assert result["execution"]["published"] is False
    assert len(result["recommendations"]) == 3
    for row in result["recommendations"]:
        assert row["recommended_sell_price"] >= row["safe_price_min"]
        assert row["max_purchase_price"] >= row["target_purchase_price"]
        assert row["recommended_order_qty"] >= 0
        assert row["workflow_status"] == "shadow_observation"
        assert row["published"] is False
