from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _actual_ratio(group: pd.DataFrame, column: str) -> float:
    if column not in group:
        return 0.0
    return float(group[column].astype(str).str.lower().eq("actual").mean())


def _infer_period_days(group: pd.DataFrame) -> int:
    deltas = (
        group.sort_values("date")["date"]
        .drop_duplicates()
        .diff()
        .dt.days.dropna()
        .clip(lower=1)
    )
    if deltas.empty:
        return 30
    median = int(round(float(deltas.median())))
    if 25 <= median <= 35:
        return 30
    if 5 <= median <= 9:
        return 7
    return max(1, median)


def _required_periods(period_days: int) -> int:
    if period_days <= 2:
        return 90
    if period_days <= 9:
        return 26
    if period_days <= 16:
        return 18
    return 12


def analyze_history_quality(history: pd.DataFrame) -> dict[str, Any]:
    per_sku: list[dict[str, Any]] = []
    for sku, group in history.groupby("sku", sort=True):
        group = group.sort_values("date")
        observations = len(group)
        period_days = _infer_period_days(group)
        required = _required_periods(period_days)
        price_points = int(group["sell_price"].round(4).nunique())
        zero_share = float((group["units"] == 0).mean())
        purchase_actual_ratio = _actual_ratio(group, "purchase_cost_source")
        stock_actual_ratio = _actual_ratio(group, "stock_source")
        daily_stock_ratio = float(
            group.get(
                "stock_history_is_daily",
                pd.Series(False, index=group.index),
            )
            .astype(bool)
            .mean()
        )
        shipping_actual_ratio = _actual_ratio(group, "shipping_cost_source")
        handling_actual_ratio = _actual_ratio(group, "handling_cost_source")
        storage_actual_ratio = _actual_ratio(group, "storage_cost_source")
        complete_logistics = (
            group.get("shipping_cost_source", pd.Series("", index=group.index))
            .astype(str)
            .str.lower()
            .eq("actual")
            & group.get("handling_cost_source", pd.Series("", index=group.index))
            .astype(str)
            .str.lower()
            .eq("actual")
            & group.get("storage_cost_source", pd.Series("", index=group.index))
            .astype(str)
            .str.lower()
            .eq("actual")
        )
        logistics_actual_ratio = float(complete_logistics.mean())
        latest = group.iloc[-1]
        purchase_cost_is_actual = (
            purchase_actual_ratio >= 0.90
            and str(latest.get("purchase_cost_source", "")).lower() == "actual"
        )
        inventory_is_actual = (
            stock_actual_ratio >= 0.80
            and daily_stock_ratio >= 0.80
            and str(latest.get("stock_source", "")).lower() == "actual"
        )
        logistics_cost_is_actual = (
            logistics_actual_ratio >= 0.80
            and str(latest.get("shipping_cost_source", "")).lower() == "actual"
            and str(latest.get("handling_cost_source", "")).lower() == "actual"
            and str(latest.get("storage_cost_source", "")).lower() == "actual"
        )
        production_data_ready = (
            purchase_cost_is_actual
            and inventory_is_actual
            and logistics_cost_is_actual
        )
        span_days = int((group["date"].max() - group["date"].min()).days)
        flags: list[str] = []
        if observations < required:
            flags.append("short_history")
        if price_points < 3:
            flags.append("insufficient_price_variation")
        if zero_share > 0.45:
            flags.append("intermittent_demand")
        if not purchase_cost_is_actual:
            flags.append("purchase_cost_not_actual")
        if not inventory_is_actual:
            flags.append("daily_inventory_not_actual")
        if not logistics_cost_is_actual:
            flags.append("logistics_cost_not_actual")
        if observations and group["stock"].eq(0).mean() > 0.25:
            flags.append("possible_stockout_censoring")
        per_sku.append(
            {
                "sku": str(sku),
                "observations": observations,
                "required_observations": required,
                "period_days": period_days,
                "history_span_days": span_days,
                "price_points": price_points,
                "zero_sales_share": round(zero_share, 4),
                "purchase_cost_is_estimated": not purchase_cost_is_actual,
                "purchase_cost_is_actual": purchase_cost_is_actual,
                "inventory_is_actual": inventory_is_actual,
                "logistics_cost_is_actual": logistics_cost_is_actual,
                "production_data_ready": production_data_ready,
                "actual_coverage": {
                    "purchase_cost": round(purchase_actual_ratio, 4),
                    "stock": round(stock_actual_ratio, 4),
                    "daily_stock_history": round(daily_stock_ratio, 4),
                    "shipping_cost": round(shipping_actual_ratio, 4),
                    "handling_cost": round(handling_actual_ratio, 4),
                    "storage_cost": round(storage_actual_ratio, 4),
                    "complete_logistics": round(logistics_actual_ratio, 4),
                },
                "eligible_history": observations >= required,
                "eligible_elasticity_test": observations >= 8 and price_points >= 3,
                "flags": flags,
            }
        )

    sku_count = len(per_sku)
    eligible_history = sum(row["eligible_history"] for row in per_sku)
    eligible_elasticity = sum(row["eligible_elasticity_test"] for row in per_sku)
    actual_purchase_cost_skus = sum(row["purchase_cost_is_actual"] for row in per_sku)
    actual_inventory_skus = sum(row["inventory_is_actual"] for row in per_sku)
    actual_logistics_skus = sum(row["logistics_cost_is_actual"] for row in per_sku)
    production_data_ready_skus = sum(row["production_data_ready"] for row in per_sku)
    estimated_cost_skus = sku_count - actual_purchase_cost_skus
    median_observations = float(
        np.median([row["observations"] for row in per_sku])
    )
    history_ratio = eligible_history / max(1, sku_count)
    elasticity_ratio = eligible_elasticity / max(1, sku_count)
    data_ready_ratio = production_data_ready_skus / max(1, sku_count)
    if (
        history_ratio >= 0.90
        and elasticity_ratio >= 0.75
        and data_ready_ratio >= 0.90
    ):
        grade = "A"
    elif history_ratio >= 0.65 and elasticity_ratio >= 0.50 and data_ready_ratio >= 0.50:
        grade = "B"
    elif history_ratio >= 0.30:
        grade = "C"
    else:
        grade = "D"

    recommendations: list[str] = []
    if eligible_history < sku_count:
        recommendations.append(
            "Добавьте транзакционную daily/weekly историю: для weekly нужно "
            "минимум 26 периодов, для monthly — 12."
        )
    if eligible_elasticity < sku_count:
        recommendations.append(
            "Для SKU без 3+ ценовых уровней используйте category fallback и "
            "контролируемый price-test ±2–3%."
        )
    if actual_purchase_cost_skus < sku_count:
        recommendations.append(
            "Замените расчетную закупочную себестоимость фактической ERP-историей."
        )
    if actual_inventory_skus < sku_count:
        recommendations.append(
            "Добавьте ежедневные WMS-снимки остатков; не подменяйте пропуск нулем."
        )
    if actual_logistics_skus < sku_count:
        recommendations.append(
            "Добавьте фактические TMS/бухгалтерские расходы: перевозку, обработку "
            "и хранение на единицу."
        )
    return {
        "grade": grade,
        "rows": len(history),
        "sku_count": sku_count,
        "date_min": history["date"].min().date().isoformat(),
        "date_max": history["date"].max().date().isoformat(),
        "median_observations_per_sku": median_observations,
        "eligible_history_skus": eligible_history,
        "eligible_elasticity_skus": eligible_elasticity,
        "estimated_cost_skus": estimated_cost_skus,
        "actual_purchase_cost_skus": actual_purchase_cost_skus,
        "actual_inventory_skus": actual_inventory_skus,
        "actual_logistics_skus": actual_logistics_skus,
        "production_data_ready_skus": production_data_ready_skus,
        "recommendations": recommendations,
        "per_sku": per_sku,
    }
