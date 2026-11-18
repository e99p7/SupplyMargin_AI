from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from supplymargin.data import NormalizedData, normalize_history, read_csv_flexible


@dataclass(slots=True)
class IntegrationResult:
    normalized: NormalizedData
    report: dict[str, Any]


COMMON_ALIASES = {
    "номенклатура": "sku",
    "код_номенклатуры": "sku",
    "код номенклатуры": "sku",
    "артикул": "sku",
    "product_id": "sku",
    "item_id": "sku",
    "дата": "date",
    "период": "date",
    "warehouse": "warehouse_id",
    "склад": "warehouse_id",
    "supplier": "supplier_id",
    "поставщик": "supplier_id",
    "заказ_поставщику": "purchase_order_id",
    "purchase_order": "purchase_order_id",
}

KIND_ALIASES = {
    "sales": {
        "количество": "units",
        "продажи": "units",
        "qty": "units",
        "quantity": "units",
        "цена": "sell_price",
        "цена_продажи": "sell_price",
        "selling_price": "sell_price",
        "категория": "category",
    },
    "purchases": {
        "себестоимость": "purchase_cost",
        "закупочная_цена": "purchase_cost",
        "цена_закупки": "purchase_cost",
        "unit_cost": "purchase_cost",
        "effective_from": "date",
    },
    "inventory": {
        "остаток": "stock",
        "остаток_на_конец": "stock",
        "on_hand": "stock",
        "quantity_on_hand": "stock",
        "в_пути": "on_order",
        "заказано": "on_order",
    },
    "logistics": {
        "доставка_на_единицу": "shipping_cost",
        "транспортные_расходы_на_единицу": "shipping_cost",
        "freight_per_unit": "shipping_cost",
        "приемка_на_единицу": "handling_cost",
        "обработка_на_единицу": "handling_cost",
        "handling_per_unit": "handling_cost",
        "хранение_единицы_в_день": "storage_cost_per_unit_day",
        "storage_per_unit_day": "storage_cost_per_unit_day",
        "транспортные_расходы_всего": "shipping_total",
        "freight_total": "shipping_total",
        "обработка_всего": "handling_total",
        "handling_total": "handling_total",
        "количество_в_поставке": "shipment_quantity",
        "shipment_qty": "shipment_quantity",
        "quantity": "shipment_quantity",
    },
}


def _standardize(frame: pd.DataFrame, kind: str) -> pd.DataFrame:
    normalized_names = {
        column: str(column).strip().lower().replace("-", "_")
        for column in frame.columns
    }
    result = frame.rename(columns=normalized_names)
    aliases = {**COMMON_ALIASES, **KIND_ALIASES[kind]}
    result = result.rename(columns={column: aliases.get(column, column) for column in result})
    return result


def _require(frame: pd.DataFrame, required: set[str], label: str) -> None:
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(
            f"В таблице {label} отсутствуют колонки: {', '.join(missing)}."
        )


def _numbers(series: pd.Series) -> pd.Series:
    return pd.to_numeric(
        series.astype(str)
        .str.replace("\u00a0", "", regex=False)
        .str.replace(" ", "", regex=False)
        .str.replace(",", ".", regex=False),
        errors="coerce",
    )


def _dates(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", dayfirst=False)


def _source_value(value: Any, default: str = "actual") -> str:
    if pd.isna(value):
        return "missing"
    normalized = str(value).strip().lower()
    aliases = {
        "fact": "actual",
        "real": "actual",
        "факт": "actual",
        "estimate": "estimated",
        "derived": "estimated",
        "оценка": "estimated",
        "none": "missing",
        "nan": "missing",
        "": "missing",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in {"actual", "estimated", "missing"} else default


def _group_source(group: pd.DataFrame, column: str, default: str = "actual") -> str:
    if column not in group:
        return default
    values = {_source_value(value, default) for value in group[column]}
    if "missing" in values:
        return "missing"
    if "estimated" in values:
        return "estimated"
    return "actual"


def _weighted_average(
    frame: pd.DataFrame,
    value: str,
    weight: str,
) -> float:
    values = _numbers(frame[value])
    weights = _numbers(frame[weight]).fillna(0).clip(lower=0)
    valid = values.notna()
    if not valid.any():
        return float("nan")
    if weights[valid].sum() <= 0:
        return float(values[valid].median())
    return float(np.average(values[valid], weights=weights[valid]))


def _prepare_sales(raw: pd.DataFrame) -> pd.DataFrame:
    frame = _standardize(raw, "sales")
    _require(frame, {"sku", "date", "units", "sell_price"}, "sales")
    frame["sku"] = frame["sku"].astype(str).str.strip()
    frame["date"] = _dates(frame["date"])
    frame["units"] = _numbers(frame["units"])
    frame["sell_price"] = _numbers(frame["sell_price"])
    frame = frame.dropna(subset=["sku", "date", "units", "sell_price"])
    frame = frame[(frame["sku"] != "") & (frame["units"] >= 0) & (frame["sell_price"] > 0)]

    passthrough = [
        column
        for column in (
            "category",
            "competitor_price",
            "min_price",
            "max_price",
            "lead_time_days",
        )
        if column in frame
    ]
    rows: list[dict[str, Any]] = []
    for (sku, date), group in frame.groupby(["sku", "date"], sort=True):
        row: dict[str, Any] = {
            "sku": sku,
            "date": date,
            "units": float(group["units"].sum()),
            "sell_price": _weighted_average(group, "sell_price", "units"),
        }
        for column in passthrough:
            non_null = group[column].dropna()
            row[column] = non_null.iloc[-1] if not non_null.empty else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def _prepare_purchases(raw: pd.DataFrame) -> pd.DataFrame:
    frame = _standardize(raw, "purchases")
    _require(frame, {"sku", "date", "purchase_cost"}, "purchase_costs")
    frame["sku"] = frame["sku"].astype(str).str.strip()
    frame["date"] = _dates(frame["date"])
    frame["purchase_cost"] = _numbers(frame["purchase_cost"])
    frame = frame.dropna(subset=["sku", "date", "purchase_cost"])
    frame = frame[(frame["sku"] != "") & (frame["purchase_cost"] > 0)]
    frame = (
        frame.sort_values(["sku", "date"])
        .groupby(["sku", "date"], as_index=False)
        .tail(1)
    )
    columns = ["sku", "date", "purchase_cost"]
    for optional in ("supplier_id", "purchase_order_id", "purchase_cost_source"):
        if optional in frame:
            columns.append(optional)
    result = frame[columns].rename(columns={"date": "purchase_effective_date"})
    if "purchase_cost_source" in result:
        result["purchase_cost_source"] = result["purchase_cost_source"].map(
            _source_value
        )
    else:
        result["purchase_cost_source"] = "actual"
    return result


def _prepare_inventory(raw: pd.DataFrame) -> pd.DataFrame:
    frame = _standardize(raw, "inventory")
    _require(frame, {"sku", "date", "stock"}, "daily_stock")
    frame["sku"] = frame["sku"].astype(str).str.strip()
    frame["date"] = _dates(frame["date"])
    frame["stock"] = _numbers(frame["stock"])
    if "on_order" not in frame:
        frame["on_order"] = 0.0
    frame["on_order"] = _numbers(frame["on_order"]).fillna(0)
    frame = frame.dropna(subset=["sku", "date", "stock"])
    frame = frame[(frame["sku"] != "") & (frame["stock"] >= 0) & (frame["on_order"] >= 0)]
    rows: list[dict[str, Any]] = []
    for (sku, date), group in frame.groupby(["sku", "date"], sort=True):
        rows.append(
            {
                "sku": sku,
                "stock_snapshot_date": date,
                "stock": float(group["stock"].sum()),
                "on_order": float(group["on_order"].sum()),
                "stock_source": _group_source(group, "stock_source"),
            }
        )
    result = pd.DataFrame(rows)
    daily_by_sku: dict[str, bool] = {}
    for sku, group in result.groupby("sku", sort=False):
        deltas = (
            group["stock_snapshot_date"]
            .drop_duplicates()
            .sort_values()
            .diff()
            .dt.days.dropna()
        )
        daily_by_sku[str(sku)] = bool(
            len(group) >= 2 and not deltas.empty and float(deltas.median()) <= 2
        )
    result["stock_history_is_daily"] = result["sku"].map(
        lambda sku: daily_by_sku.get(str(sku), False)
    )
    return result


def _prepare_logistics(raw: pd.DataFrame) -> pd.DataFrame:
    frame = _standardize(raw, "logistics")
    _require(frame, {"sku", "date"}, "logistics_costs")
    frame["sku"] = frame["sku"].astype(str).str.strip()
    frame["date"] = _dates(frame["date"])

    if "shipment_quantity" in frame:
        frame["shipment_quantity"] = _numbers(frame["shipment_quantity"])
    else:
        frame["shipment_quantity"] = 1.0
    quantity = frame["shipment_quantity"].where(frame["shipment_quantity"] > 0)

    if "shipping_cost" not in frame and "shipping_total" in frame:
        frame["shipping_cost"] = _numbers(frame["shipping_total"]) / quantity
    if "handling_cost" not in frame and "handling_total" in frame:
        frame["handling_cost"] = _numbers(frame["handling_total"]) / quantity

    available_costs = {
        "shipping_cost": "shipping_cost_source",
        "handling_cost": "handling_cost_source",
        "storage_cost_per_unit_day": "storage_cost_source",
    }
    for value_column in available_costs:
        if value_column in frame:
            frame[value_column] = _numbers(frame[value_column])
    frame = frame.dropna(subset=["sku", "date"])
    frame = frame[frame["sku"] != ""]

    rows: list[dict[str, Any]] = []
    for (sku, date), group in frame.groupby(["sku", "date"], sort=True):
        row: dict[str, Any] = {
            "sku": sku,
            "logistics_effective_date": date,
        }
        for value_column, source_column in available_costs.items():
            if value_column in group and group[value_column].notna().any():
                row[value_column] = max(
                    0.0,
                    _weighted_average(group, value_column, "shipment_quantity"),
                )
                row[source_column] = _group_source(group, source_column)
            else:
                row[value_column] = 0.0
                row[source_column] = "missing"
        rows.append(row)
    return pd.DataFrame(rows)


def _merge_asof_per_sku(
    left: pd.DataFrame,
    right: pd.DataFrame,
    *,
    right_date: str,
    tolerance_days: int | None,
) -> pd.DataFrame:
    right_value_columns = [column for column in right.columns if column != "sku"]
    pieces: list[pd.DataFrame] = []
    tolerance = (
        pd.to_timedelta(int(tolerance_days), unit="D")
        if tolerance_days is not None
        else None
    )
    for sku, left_group in left.groupby("sku", sort=False):
        right_group = right[right["sku"] == sku][right_value_columns]
        if right_group.empty:
            merged = left_group.copy()
            for column in right_value_columns:
                merged[column] = np.nan
        else:
            merged = pd.merge_asof(
                left_group.sort_values("date"),
                right_group.sort_values(right_date),
                left_on="date",
                right_on=right_date,
                direction="backward",
                tolerance=tolerance,
            )
        pieces.append(merged)
    return pd.concat(pieces, ignore_index=True).sort_values(["sku", "date"])


def _coverage(frame: pd.DataFrame, column: str) -> float:
    if frame.empty:
        return 0.0
    return round(float(frame[column].eq("actual").mean()), 4)


def integrate_operational_tables(
    *,
    sales: pd.DataFrame,
    purchase_costs: pd.DataFrame,
    daily_stock: pd.DataFrame,
    logistics_costs: pd.DataFrame,
    inventory_tolerance_days: int = 7,
    logistics_tolerance_days: int = 180,
) -> IntegrationResult:
    """Join real ERP/WMS/TMS exports into the canonical model history.

    Purchase and logistics costs are joined as the latest known facts as of
    each sales date. Inventory uses the latest snapshot no older than the
    configured tolerance. No missing cost is silently labelled as factual.
    """

    sales_ready = _prepare_sales(sales)
    purchases_ready = _prepare_purchases(purchase_costs)
    inventory_ready = _prepare_inventory(daily_stock)
    logistics_ready = _prepare_logistics(logistics_costs)
    if sales_ready.empty:
        raise ValueError("В sales не осталось корректных строк.")

    joined = _merge_asof_per_sku(
        sales_ready,
        purchases_ready,
        right_date="purchase_effective_date",
        tolerance_days=None,
    )
    joined = _merge_asof_per_sku(
        joined,
        inventory_ready,
        right_date="stock_snapshot_date",
        tolerance_days=inventory_tolerance_days,
    )
    joined = _merge_asof_per_sku(
        joined,
        logistics_ready,
        right_date="logistics_effective_date",
        tolerance_days=logistics_tolerance_days,
    )

    joined["purchase_cost_age_days"] = (
        joined["date"] - joined["purchase_effective_date"]
    ).dt.days
    joined["stock_staleness_days"] = (
        joined["date"] - joined["stock_snapshot_date"]
    ).dt.days
    joined["logistics_cost_age_days"] = (
        joined["date"] - joined["logistics_effective_date"]
    ).dt.days

    source_defaults = {
        "purchase_cost_source": "missing",
        "stock_source": "missing",
        "shipping_cost_source": "missing",
        "handling_cost_source": "missing",
        "storage_cost_source": "missing",
    }
    for column, default in source_defaults.items():
        joined[column] = joined[column].fillna(default)
    for column in (
        "stock",
        "on_order",
        "shipping_cost",
        "handling_cost",
        "storage_cost_per_unit_day",
    ):
        joined[column] = joined[column].fillna(0.0)
    if "category" not in joined:
        joined["category"] = "unknown"
    if "lead_time_days" not in joined:
        joined["lead_time_days"] = 14

    input_rows = len(joined)
    normalized = normalize_history(joined)
    normalized.source_kind = "integrated_erp_wms_tms"
    dropped_without_purchase_cost = input_rows - len(normalized.frame)
    if dropped_without_purchase_cost:
        normalized.warnings.insert(
            0,
            f"Не использовано строк без фактической закупочной цены: "
            f"{dropped_without_purchase_cost}.",
        )
    if normalized.frame["stock_source"].ne("actual").any():
        normalized.warnings.append(
            "Для части дат нет свежего WMS-снимка; stock=0 помечен как missing."
        )
    logistics_actual = (
        normalized.frame["shipping_cost_source"].eq("actual")
        & normalized.frame["handling_cost_source"].eq("actual")
        & normalized.frame["storage_cost_source"].eq("actual")
    )
    if not logistics_actual.all():
        normalized.warnings.append(
            "Для части строк логистическая себестоимость неполная; "
            "quality-gate не считает такие строки фактическими."
        )

    report = {
        "source_kind": normalized.source_kind,
        "sales_rows": len(sales_ready),
        "purchase_cost_rows": len(purchases_ready),
        "inventory_rows": len(inventory_ready),
        "logistics_rows": len(logistics_ready),
        "integrated_rows": len(normalized.frame),
        "sku_count": int(normalized.frame["sku"].nunique()),
        "date_min": normalized.frame["date"].min().date().isoformat(),
        "date_max": normalized.frame["date"].max().date().isoformat(),
        "actual_coverage": {
            "purchase_cost": _coverage(normalized.frame, "purchase_cost_source"),
            "stock": _coverage(normalized.frame, "stock_source"),
            "daily_stock_history": round(
                float(normalized.frame["stock_history_is_daily"].astype(bool).mean()),
                4,
            ),
            "shipping_cost": _coverage(normalized.frame, "shipping_cost_source"),
            "handling_cost": _coverage(normalized.frame, "handling_cost_source"),
            "storage_cost": _coverage(normalized.frame, "storage_cost_source"),
            "complete_logistics": round(float(logistics_actual.mean()), 4),
        },
        "warnings": normalized.warnings,
    }
    return IntegrationResult(normalized=normalized, report=report)


def integrate_operational_files(
    *,
    sales: str | Path | bytes,
    purchase_costs: str | Path | bytes,
    daily_stock: str | Path | bytes,
    logistics_costs: str | Path | bytes,
    inventory_tolerance_days: int = 7,
    logistics_tolerance_days: int = 180,
) -> IntegrationResult:
    return integrate_operational_tables(
        sales=read_csv_flexible(sales),
        purchase_costs=read_csv_flexible(purchase_costs),
        daily_stock=read_csv_flexible(daily_stock),
        logistics_costs=read_csv_flexible(logistics_costs),
        inventory_tolerance_days=inventory_tolerance_days,
        logistics_tolerance_days=logistics_tolerance_days,
    )
