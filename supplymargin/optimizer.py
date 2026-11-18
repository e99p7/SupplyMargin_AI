from __future__ import annotations

import math
import zlib
from dataclasses import asdict, dataclass
from typing import Any, Callable

import numpy as np
import pandas as pd

from supplymargin.elasticity import ElasticityEstimate
from supplymargin.forecast import ForecastResult


@dataclass(frozen=True, slots=True)
class OptimizationPolicy:
    min_margin: float = 0.15
    max_price_change: float = 0.15
    service_level: float = 0.90
    risk_aversion: float = 0.25
    random_seed: int = 42
    simulations: int = 240


@dataclass(slots=True)
class CandidateResult:
    price: float
    order_qty: float
    demand_mean: float
    expected_profit: float
    profit_p10: float
    objective: float
    expected_leftover: float
    expected_lost_sales: float


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if np.isfinite(result) else default


def _evaluate_candidate(
    *,
    price: float,
    reference_price: float,
    elasticity: float,
    forecast: ForecastResult,
    available_stock: float,
    landed_cost: float,
    storage_cost_per_day: float,
    horizon_days: int,
    policy: OptimizationPolicy,
    rng: np.random.Generator,
) -> CandidateResult:
    price_ratio = max(0.01, price / max(reference_price, 0.01))
    demand_mean = max(0.0, forecast.mean * math.pow(price_ratio, elasticity))
    source_sigma = max(0.25, (forecast.p90 - forecast.p10) / 2.5632)
    demand_sigma = max(0.25, source_sigma * math.pow(price_ratio, elasticity))
    samples = np.maximum(
        0.0,
        rng.normal(demand_mean, demand_sigma, size=policy.simulations),
    )
    target_stock = float(np.quantile(samples, policy.service_level))
    order_qty = max(0.0, math.ceil(target_stock - available_stock))
    total_available = available_stock + order_qty
    sold = np.minimum(samples, total_available)
    leftover = np.maximum(0.0, total_available - samples)
    lost_sales = np.maximum(0.0, samples - total_available)

    # Accounting contribution: all sold units receive the current replacement
    # landed cost, including units already in stock. This avoids overstating
    # price recommendations by treating current inventory as free.
    gross_profit = (price - landed_cost) * sold
    holding_penalty = storage_cost_per_day * horizon_days * leftover
    lost_sale_penalty = np.maximum(0.0, price - landed_cost) * 0.12 * lost_sales
    profits = gross_profit - holding_penalty - lost_sale_penalty
    expected = float(np.mean(profits))
    p10 = float(np.quantile(profits, 0.10))
    downside = max(0.0, expected - p10)
    objective = expected - policy.risk_aversion * downside
    return CandidateResult(
        price=price,
        order_qty=order_qty,
        demand_mean=demand_mean,
        expected_profit=expected,
        profit_p10=p10,
        objective=objective,
        expected_leftover=float(np.mean(leftover)),
        expected_lost_sales=float(np.mean(lost_sales)),
    )

from collections.abc import Mapping

import math
import pandas as pd


_FINANCIAL_INPUTS = {
    "sell_price": "цена продажи",
    "purchase_cost": "себестоимость",
    "shipping_cost": "стоимость логистики",
    "handling_cost": "стоимость обработки",
    "storage_cost_per_unit_day": "стоимость хранения",
    "stock": "остатки",
    "on_order": "заказы в пути",
}

_FINANCIAL_PROVENANCE = {
    "purchase_cost_source": "фактическая себестоимость",
    "stock_source": "фактические остатки",
    "shipping_cost_source": "фактическая логистика",
    "handling_cost_source": "фактическая стоимость обработки",
    "storage_cost_source": "фактическая стоимость хранения",
}


def validate_financial_inputs(row: Mapping) -> None:
    """Reject incomplete or non-actual data before financial optimization."""
    missing = []

    for column, label in _FINANCIAL_INPUTS.items():
        value = row.get(column)

        try:
            number = float(value)
            valid = math.isfinite(number)
        except (TypeError, ValueError):
            valid = False

        if not valid:
            missing.append(label)
            continue

        if column in {"sell_price", "purchase_cost"} and number <= 0:
            missing.append(label)

        if column in {"shipping_cost", "handling_cost",
                      "storage_cost_per_unit_day", "stock", "on_order"}:
            if number < 0:
                missing.append(label)

    for column, label in _FINANCIAL_PROVENANCE.items():
        if row.get(column) != "actual":
            missing.append(label)

    if missing:
        unique_missing = sorted(set(missing))
        raise ValueError(
            "Финансовая оптимизация запрещена: отсутствуют или не подтверждены "
            "обязательные данные: " + ", ".join(unique_missing)
        )




def _evaluate_candidate_grid(
    *,
    prices: np.ndarray,
    reference_price: float,
    elasticity: float,
    forecast: ForecastResult,
    available_stock: float,
    landed_cost: float,
    storage_cost_per_day: float,
    horizon_days: int,
    policy: OptimizationPolicy,
    seed: int,
) -> list[CandidateResult]:
    """Evaluate all candidate prices in one NumPy batch.

    Keeps the same deterministic random stream used by the previous per-price
    implementation, but evaluates the resulting samples in one NumPy batch.
    This preserves simulation count and candidate-level Monte Carlo draws while
    avoiding repeated Python-level calculation loops.
    """
    prices = np.asarray(prices, dtype=float)
    ratios = np.maximum(0.01, prices / max(reference_price, 0.01))
    demand_means = np.maximum(0.0, forecast.mean * np.power(ratios, elasticity))
    source_sigma = max(0.25, (forecast.p90 - forecast.p10) / 2.5632)
    demand_sigmas = np.maximum(0.25, source_sigma * np.power(ratios, elasticity))

    standard_normal = np.stack([
        np.random.default_rng(seed + index).normal(size=policy.simulations)
        for index in range(len(prices))
    ])
    samples = np.maximum(
        0.0,
        demand_means[:, None] + demand_sigmas[:, None] * standard_normal,
    )
    target_stock = np.quantile(samples, policy.service_level, axis=1)
    order_qty = np.maximum(0.0, np.ceil(target_stock - available_stock))
    total_available = available_stock + order_qty
    sold = np.minimum(samples, total_available[:, None])
    leftover = np.maximum(0.0, total_available[:, None] - samples)
    lost_sales = np.maximum(0.0, samples - total_available[:, None])

    gross_profit = (prices[:, None] - landed_cost) * sold
    holding_penalty = storage_cost_per_day * horizon_days * leftover
    lost_sale_penalty = (
        np.maximum(0.0, prices[:, None] - landed_cost) * 0.12 * lost_sales
    )
    profits = gross_profit - holding_penalty - lost_sale_penalty
    expected = np.mean(profits, axis=1)
    p10 = np.quantile(profits, 0.10, axis=1)
    objective = expected - policy.risk_aversion * np.maximum(0.0, expected - p10)
    leftover_mean = np.mean(leftover, axis=1)
    lost_mean = np.mean(lost_sales, axis=1)

    return [
        CandidateResult(
            price=float(prices[i]),
            order_qty=float(order_qty[i]),
            demand_mean=float(demand_means[i]),
            expected_profit=float(expected[i]),
            profit_p10=float(p10[i]),
            objective=float(objective[i]),
            expected_leftover=float(leftover_mean[i]),
            expected_lost_sales=float(lost_mean[i]),
        )
        for i in range(len(prices))
    ]


def optimize_sku(
    *,
    latest: pd.Series,
    forecast: ForecastResult,
    elasticity: ElasticityEstimate,
    market: dict[str, Any],
    policy: OptimizationPolicy,
) -> dict[str, Any]:
    validate_financial_inputs(latest)

    sku = str(latest["sku"])
    current_price = _safe_float(latest["sell_price"])
    purchase_cost = _safe_float(latest["purchase_cost"])
    shipping = _safe_float(latest.get("shipping_cost"))
    handling = _safe_float(latest.get("handling_cost"))
    storage_day = _safe_float(latest.get("storage_cost_per_unit_day"))
    lead_days = max(0, int(_safe_float(latest.get("lead_time_days"), 14)))
    stock = max(0.0, _safe_float(latest.get("stock")))
    on_order = max(0.0, _safe_float(latest.get("on_order")))
    available_stock = stock + on_order

    key_rate = _safe_float(market.get("actual", {}).get("key_rate_percent"), 14.0)
    period_days = forecast.period_days
    horizon_days = max(1, forecast.horizon * period_days)
    capital_cost = purchase_cost * (key_rate / 100.0) * lead_days / 365.0
    lead_storage = storage_day * lead_days
    landed_cost = purchase_cost + shipping + handling + capital_cost + lead_storage

    economic_floor = landed_cost / max(0.01, 1.0 - policy.min_margin)
    row_min = _safe_float(latest.get("min_price"), 0.0)
    row_max = _safe_float(latest.get("max_price"), 0.0)
    guard_min = current_price * (1.0 - policy.max_price_change)
    guard_max = current_price * (1.0 + policy.max_price_change)
    safe_min = max(economic_floor, guard_min, row_min)
    safe_max = guard_max
    if row_max > 0:
        safe_max = min(safe_max, row_max)
    floor_forced = safe_min > safe_max
    if floor_forced:
        safe_max = safe_min * 1.05
    safe_max = max(safe_max, safe_min + 0.01)

    seed = policy.random_seed + zlib.crc32(sku.encode("utf-8"))
    grid = np.linspace(safe_min, safe_max, 61)
    candidates = _evaluate_candidate_grid(
        prices=grid,
        reference_price=current_price,
        elasticity=elasticity.elasticity,
        forecast=forecast,
        available_stock=available_stock,
        landed_cost=landed_cost,
        storage_cost_per_day=storage_day,
        horizon_days=horizon_days,
        policy=policy,
        seed=seed,
    )
    best = max(candidates, key=lambda x: x.objective)
    baseline_rng = np.random.default_rng(seed + 100_000)
    baseline = _evaluate_candidate(
        price=current_price,
        reference_price=current_price,
        elasticity=elasticity.elasticity,
        forecast=forecast,
        available_stock=available_stock,
        landed_cost=landed_cost,
        storage_cost_per_day=storage_day,
        horizon_days=horizon_days,
        policy=policy,
        rng=baseline_rng,
    )

    non_purchase_cost = shipping + handling + capital_cost + lead_storage
    risk_buffer = storage_day * horizon_days * 0.5
    max_purchase_price = max(
        0.0,
        best.price * (1.0 - policy.min_margin) - non_purchase_cost - risk_buffer,
    )
    base_scenario = market.get("scenarios", {}).get("base", {})
    annual_cost_growth = _safe_float(base_scenario.get("cost_growth_percent"), 6.5)
    horizon_years = horizon_days / 365.0
    expected_market_cost = purchase_cost * (
        1.0 + annual_cost_growth / 100.0 * horizon_years
    )
    target_purchase_price = min(
        purchase_cost,
        expected_market_cost * 0.98,
        max_purchase_price * 0.92,
    )
    target_purchase_price = max(0.0, target_purchase_price)

    margin = (best.price - landed_cost) / max(best.price, 0.01)
    purchase_headroom = max_purchase_price - purchase_cost
    stress = market.get("scenarios", {}).get("stress", {})
    stress_demand = _safe_float(stress.get("demand_factor"), 0.90)
    stress_cost = 1.0 + _safe_float(stress.get("cost_growth_percent"), 9.0) / 100
    stress_profit_proxy = (
        (best.price - landed_cost * stress_cost)
        * min(available_stock + best.order_qty, best.demand_mean * stress_demand)
        - storage_day
        * horizon_days
        * max(0.0, available_stock + best.order_qty - best.demand_mean * stress_demand)
    )

    flags: list[str] = []
    if floor_forced:
        flags.append("price_floor_above_change_guardrail")
    if purchase_headroom < 0:
        flags.append("supplier_price_above_walk_away")
    if elasticity.confidence == "low":
        flags.append("low_elasticity_confidence")
    purchase_source = str(latest.get("purchase_cost_source", "missing"))
    stock_source = str(latest.get("stock_source", "missing"))
    shipping_source = str(latest.get("shipping_cost_source", "missing"))
    handling_source = str(latest.get("handling_cost_source", "missing"))
    storage_source = str(latest.get("storage_cost_source", "missing"))
    if purchase_source != "actual":
        flags.append("purchase_cost_not_actual")
    if stock_source != "actual":
        flags.append("daily_inventory_not_actual")
    if not all(
        source == "actual"
        for source in (shipping_source, handling_source, storage_source)
    ):
        flags.append("logistics_cost_not_actual")
    if best.expected_lost_sales > max(1.0, best.demand_mean * 0.05):
        flags.append("stockout_risk")
    if best.expected_leftover > max(1.0, best.demand_mean * 0.20):
        flags.append("overstock_risk")

    return {
        "sku": sku,
        "category": str(latest.get("category", "unknown")),
        "current_sell_price": round(current_price, 2),
        "recommended_sell_price": round(best.price, 2),
        "safe_price_min": round(safe_min, 2),
        "safe_price_max": round(safe_max, 2),
        "current_purchase_price": round(purchase_cost, 2),
        "target_purchase_price": round(target_purchase_price, 2),
        "max_purchase_price": round(max_purchase_price, 2),
        "purchase_price_headroom": round(purchase_headroom, 2),
        "landed_unit_cost": round(landed_cost, 2),
        "recommended_order_qty": int(best.order_qty),
        "stock_plus_on_order": round(available_stock, 2),
        "forecast_units": round(forecast.mean, 2),
        "forecast_p10": round(forecast.p10, 2),
        "forecast_p90": round(forecast.p90, 2),
        "price_adjusted_demand": round(best.demand_mean, 2),
        "elasticity": round(elasticity.elasticity, 4),
        "elasticity_confidence": elasticity.confidence,
        "elasticity_source": elasticity.source,
        "expected_margin_percent": round(margin * 100.0, 2),
        "expected_profit_before": round(baseline.expected_profit, 2),
        "expected_profit_after": round(best.expected_profit, 2),
        "profit_p10_after": round(best.profit_p10, 2),
        "stress_profit_proxy": round(float(stress_profit_proxy), 2),
        "expected_leftover": round(best.expected_leftover, 2),
        "expected_lost_sales": round(best.expected_lost_sales, 2),
        "data_lineage": {
            "purchase_cost": purchase_source,
            "stock": stock_source,
            "shipping_cost": shipping_source,
            "handling_cost": handling_source,
            "storage_cost": storage_source,
        },
        "flags": flags,
    }


def optimize_portfolio(
    *,
    history: pd.DataFrame,
    forecasts: list[ForecastResult],
    elasticities: dict[str, ElasticityEstimate],
    market: dict[str, Any],
    policy: OptimizationPolicy,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[dict[str, Any]]:
    forecast_map = {item.sku: item for item in forecasts}
    latest_rows = (
        history.sort_values("date")
        .groupby("sku", as_index=False)
        .tail(1)
    )

    for _, latest in latest_rows.iterrows():
        sku = str(latest["sku"])
        if sku in forecast_map and sku in elasticities:
            validate_financial_inputs(latest)

    results: list[dict[str, Any]] = []
    total_rows = len(latest_rows)
    for row_index, (_, latest) in enumerate(latest_rows.iterrows(), start=1):
        sku = str(latest["sku"])
        if sku in forecast_map and sku in elasticities:
            results.append(
                optimize_sku(
                    latest=latest,
                    forecast=forecast_map[sku],
                    elasticity=elasticities[sku],
                    market=market,
                    policy=policy,
                )
            )
        if progress_callback is not None:
            progress_callback(row_index, total_rows)
    return sorted(results, key=lambda row: row["expected_profit_after"], reverse=True)


def policy_dict(policy: OptimizationPolicy) -> dict[str, Any]:
    return asdict(policy)
