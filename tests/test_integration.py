from __future__ import annotations

import pandas as pd

from supplymargin.integration import integrate_operational_tables
from supplymargin.quality import analyze_history_quality
from tests.test_pipeline import synthetic_history


def operational_tables() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    history = synthetic_history()
    sales = history[
        ["sku", "date", "units", "sell_price", "category", "competitor_price"]
    ].copy()
    purchases = history[["sku", "date", "purchase_cost"]].copy()
    inventory_rows = []
    for sku in history["sku"].unique():
        for date in pd.date_range(history["date"].min(), history["date"].max(), freq="D"):
            inventory_rows.append(
                {
                    "sku": sku,
                    "date": date,
                    "stock": 25,
                    "warehouse_id": "MAIN",
                    "on_order": 2,
                }
            )
    inventory = pd.DataFrame(inventory_rows)
    logistics = history[
        ["sku", "date", "shipping_cost", "handling_cost", "storage_cost_per_unit_day"]
    ].copy()
    return sales, purchases, inventory, logistics


def test_operational_tables_are_joined_with_actual_lineage() -> None:
    sales, purchases, inventory, logistics = operational_tables()
    result = integrate_operational_tables(
        sales=sales,
        purchase_costs=purchases,
        daily_stock=inventory,
        logistics_costs=logistics,
    )

    frame = result.normalized.frame
    assert result.normalized.source_kind == "integrated_erp_wms_tms"
    assert len(frame) == len(sales)
    assert frame["purchase_cost_source"].eq("actual").all()
    assert frame["stock_source"].eq("actual").all()
    assert frame["shipping_cost_source"].eq("actual").all()
    assert frame["handling_cost_source"].eq("actual").all()
    assert frame["storage_cost_source"].eq("actual").all()
    assert result.report["actual_coverage"]["complete_logistics"] == 1.0

    quality = analyze_history_quality(frame)
    assert quality["production_data_ready_skus"] == quality["sku_count"] == 3
    assert all(row["production_data_ready"] for row in quality["per_sku"])


def test_missing_logistics_component_stays_blocked() -> None:
    sales, purchases, inventory, logistics = operational_tables()
    logistics = logistics.drop(columns=["storage_cost_per_unit_day"])
    result = integrate_operational_tables(
        sales=sales,
        purchase_costs=purchases,
        daily_stock=inventory,
        logistics_costs=logistics,
    )
    quality = analyze_history_quality(result.normalized.frame)

    assert result.report["actual_coverage"]["storage_cost"] == 0.0
    assert quality["actual_logistics_skus"] == 0
    assert all(
        "logistics_cost_not_actual" in row["flags"]
        for row in quality["per_sku"]
    )
