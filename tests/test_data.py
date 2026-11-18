from __future__ import annotations

import pandas as pd

from supplymargin.data import normalize_history


def test_canonical_normalization() -> None:
    frame = pd.DataFrame(
        {
            "sku": ["A", "A"],
            "date": ["2026-01-01", "2026-01-02"],
            "units": [2, 3],
            "sell_price": ["100,0", "101"],
            "purchase_cost": [60, 60],
            "shipping_cost": [5, 5],
            "stock": [10, 8],
        }
    )
    normalized = normalize_history(frame)
    assert normalized.source_kind == "canonical"
    assert normalized.frame["sell_price"].tolist() == [100.0, 101.0]
    assert normalized.frame["lead_time_days"].tolist() == [14, 14]


def test_legacy_cost_is_explicitly_estimated() -> None:
    frame = pd.DataFrame(
        {
            "product_id": ["bed1"],
            "month_year": ["01-05-2017"],
            "qty": [1],
            "unit_price": [45.95],
            "freight_price": [15.1],
            "product_category_name": ["bed"],
            "comp_1": [50],
            "comp_2": [49],
            "comp_3": [51],
        }
    )
    normalized = normalize_history(frame)
    assert normalized.source_kind == "legacy_real_retail_demo"
    assert bool(normalized.frame.loc[0, "purchase_cost_is_estimated"])
    assert normalized.frame.loc[0, "purchase_cost_source"] == "estimated"
    assert normalized.frame.loc[0, "stock_source"] == "estimated"
    assert normalized.frame.loc[0, "shipping_cost_source"] == "actual"
    assert normalized.frame.loc[0, "handling_cost_source"] == "estimated"
    assert normalized.frame.loc[0, "storage_cost_source"] == "estimated"
    assert normalized.frame.loc[0, "competitor_price"] == 50
