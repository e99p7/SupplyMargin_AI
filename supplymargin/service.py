from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from supplymargin.config import settings
from supplymargin.data import NormalizedData, normalize_history
from supplymargin.elasticity import estimate_elasticities
from supplymargin.forecast import FittedDemandModel, fit_demand_model, model_metadata
from supplymargin.optimizer import OptimizationPolicy, optimize_portfolio
from supplymargin.quality import analyze_history_quality
from supplymargin.schemas import OptimizeOptions
from supplymargin.storage import RunStore
from supplymargin.workflow import apply_execution_policy

def _financial_data_ready(history: pd.DataFrame) -> tuple[bool, list[str]]:
    """Check whether financial optimization has sufficient actual inputs."""
    latest = history.sort_values("date").groupby("sku").tail(1)
    required_values = {
        "sell_price": "цена продажи",
        "purchase_cost": "себестоимость",
        "shipping_cost": "стоимость логистики",
        "handling_cost": "стоимость обработки",
        "storage_cost_per_unit_day": "стоимость хранения",
        "stock": "остатки",
        "on_order": "заказы в пути",
    }
    missing: list[str] = []

    for column, label in required_values.items():
        if column not in latest.columns:
            missing.append(label)
            continue

        values = pd.to_numeric(latest[column], errors="coerce")
        if not values.notna().all():
            missing.append(label)

    for column, label in (
        ("sell_price", "цена продажи"),
        ("purchase_cost", "себестоимость"),
    ):
        if column in latest.columns:
            values = pd.to_numeric(latest[column], errors="coerce")
            if (values <= 0).any():
                if label not in missing:
                    missing.append(label)

    provenance = {
        "purchase_cost_source": "фактическая себестоимость",
        "stock_source": "фактические остатки",
        "shipping_cost_source": "фактическая логистика",
        "handling_cost_source": "фактическая обработка",
        "storage_cost_source": "фактическое хранение",
    }

    for column, label in provenance.items():
        if column not in latest.columns:
            missing.append(label)
        elif not latest[column].eq("actual").all():
            missing.append(label)

    return not missing, sorted(set(missing))


def run_optimization(
    *,
    normalized: NormalizedData,
    market: dict[str, Any],
    options: OptimizeOptions,
    store: RunStore | None = None,
    output_dir: str | Path | None = None,
    run_id: str | None = None,
    model_callback: Callable[[FittedDemandModel, dict[str, Any]], None] | None = None,
    progress_callback: Callable[[int, str, str], None] | None = None,
    force_forecast_only: bool = False,
) -> dict[str, Any]:
    history = normalized.frame.copy()
    run_id = run_id or f"run_{uuid.uuid4().hex[:16]}"
    created_at = datetime.now(timezone.utc).isoformat()

    last_progress: tuple[int, str] | None = None

    def report(percent: int, stage: str, message: str) -> None:
        nonlocal last_progress
        if progress_callback is None:
            return
        key = (int(percent), stage)
        # Avoid committing progress for every SKU: only persist actual progress
        # changes, keeping database I/O from becoming a new bottleneck.
        if key == last_progress:
            return
        last_progress = key
        progress_callback(percent, stage, message)

    # 1. Проверяем доступность фактических данных для финансового расчёта.
    report(8, "prepare", "Проверка и подготовка истории продаж")
    financial_ready, missing_financial = _financial_data_ready(history)
    if force_forecast_only:
        financial_ready = False
        missing_financial = sorted(set(missing_financial + [
            "демо-режим не подтверждает фактические финансовые данные"
        ]))

    # 2. Обучаем модель только на истории спроса.
    report(15, "training", "Построение признаков и обучение модели спроса")
    model = fit_demand_model(
        history,
        random_seed=options.random_seed,
    )
    metadata = model_metadata(model)
    report(35, "training_complete", "Модель обучена; проверка качества прогноза")

    if model_callback is not None:
        model_callback(model, metadata)

    base_demand_factor = float(
        market.get("scenarios", {})
        .get("base", {})
        .get("demand_factor", 1.0)
    )

    report(40, "forecast", "Расчёт прогноза спроса по SKU")
    forecasts = model.forecast(
        history,
        horizon=options.horizon,
        demand_factor=base_demand_factor,
        progress_callback=lambda done, total: report(
            40 + int(10 * done / max(total, 1)),
            "forecast",
            f"Рассчитаны шаги прогноза: {done} из {total}",
        ),
    )
    report(52, "forecast_complete", "Прогноз спроса рассчитан")

    # 3. Если финансовых данных недостаточно, возвращаем только прогноз.
    if not financial_ready:
        forecast_rows = [
            {
                "sku": item.sku,
                "recommendation_type": "demand_forecast",
                "forecast_units": round(item.mean, 2),
                "forecast_p10": round(item.p10, 2),
                "forecast_p90": round(item.p90, 2),
                "horizon_periods": item.horizon,
                "period_days": item.period_days,
                "forecast_path": item.path,
            }
            for item in forecasts
        ]

        warnings = list(normalized.warnings)
        warnings.append(
            "Выполнен только прогноз спроса. Финансовая оптимизация отключена: "
            "недостаточно подтверждённых фактических данных."
        )
        warnings.append(
            "Не хватает данных: " + ", ".join(missing_financial) + "."
        )

        summary = {
            "run_id": run_id,
            "created_at": created_at,
            "as_of": str(history["date"].max().date()),
            "rows_used": len(history),
            "sku_count": len(forecast_rows),
            "mode": "demand_forecast",
            "model_name": metadata["model_name"],
            "validation_wape": (
                round(float(metadata["validation_wape"]), 5)
                if metadata.get("validation_available", True) else None
            ),
            "validation_mae": (
                round(float(metadata["validation_mae"]), 5)
                if metadata.get("validation_available", True) else None
            ),
            "validation_available": metadata.get("validation_available", True),
            "horizon_periods": options.horizon,
            "expected_profit_before": None,
            "expected_profit_after": None,
            "expected_profit_gain": None,
            "execution_mode": "forecast_only",
        }

        report(92, "results", "Формирование результата прогноза")
        payload = {
            "summary": summary,
            # Здесь только прогнозы. Ценовых рекомендаций нет.
            "recommendations": forecast_rows,
            "forecasts": forecast_rows,
            "market": market,
            "warnings": warnings,
            "model_metadata": metadata,
            "options": options.model_dump(),
            "execution": {
                "mode": "forecast_only",
                "financial_optimization_skipped": True,
                "missing_financial_data": missing_financial,
            },
            "data_lineage": {
                "source_kind": normalized.source_kind,
                "financial_optimization_available": False,
                "missing_financial_data": missing_financial,
            },
        }
        return payload

    # 4. Полная оптимизация выполняется только при готовых данных.
    report(55, "elasticity", "Оценка ценовой эластичности")
    elasticities = estimate_elasticities(history)

    policy = OptimizationPolicy(
        min_margin=options.min_margin,
        max_price_change=options.max_price_change,
        service_level=options.service_level,
        risk_aversion=options.risk_aversion,
        random_seed=options.random_seed,
    )

    report(60, "optimization", "Оптимизация цен и запасов")
    recommendations = optimize_portfolio(
        history=history,
        forecasts=forecasts,
        elasticities=elasticities,
        market=market,
        policy=policy,
        progress_callback=lambda done, total: report(
            60 + int(30 * done / max(total, 1)),
            "optimization",
            f"Обработано SKU: {done} из {total}",
        ),
    )
    report(92, "quality", "Проверка качества и формирование рекомендаций")

    if not recommendations:
        raise ValueError(
            "Оптимизатор не сформировал ни одной рекомендации."
        )

    history_quality = analyze_history_quality(history)

    execution = apply_execution_policy(
        recommendations=recommendations,
        validation_wape=float(metadata["validation_wape"]),
        history_quality=history_quality,
        settings=settings,
    )

    before = float(
        sum(row["expected_profit_before"] for row in recommendations)
    )
    after = float(
        sum(row["expected_profit_after"] for row in recommendations)
    )

    summary = {
        "run_id": run_id,
        "created_at": created_at,
        "as_of": str(market.get("as_of", history["date"].max().date())),
        "rows_used": len(history),
        "sku_count": len(recommendations),
        "mode": "full_optimization",
        "model_name": metadata["model_name"],
        "validation_wape": round(float(metadata["validation_wape"]), 5),
        "horizon_periods": options.horizon,
        "expected_profit_before": round(before, 2),
        "expected_profit_after": round(after, 2),
        "expected_profit_gain": round(after - before, 2),
        "low_confidence_skus": sum(
            row["elasticity_confidence"] == "low"
            for row in recommendations
        ),
        "execution_mode": settings.execution_mode,
        "history_quality_grade": history_quality["grade"],
        "production_data_ready_skus": history_quality[
            "production_data_ready_skus"
        ],
    }

    warnings = list(normalized.warnings)
    if summary["validation_wape"] > 0.5:
        warnings.append(
            "Validation WAPE выше 50%: рекомендации следует использовать "
            "в shadow-mode и улучшить историю/признаки."
        )
    if summary["low_confidence_skus"]:
        warnings.append(
            "Для части SKU мало ценовых вариаций: применен fallback "
            "эластичности. Проведите контролируемый price-test."
        )

    report(95, "results", "Сохранение итоговых рекомендаций")
    payload = {
        "summary": summary,
        "recommendations": recommendations,
        "forecasts": [
            {
                "sku": item.sku,
                "forecast_units": round(item.mean, 2),
                "forecast_p10": round(item.p10, 2),
                "forecast_p90": round(item.p90, 2),
                "horizon_periods": item.horizon,
                "period_days": item.period_days,
                "forecast_path": item.path,
            }
            for item in forecasts
        ],
        "market": market,
        "warnings": warnings,
        "model_metadata": metadata,
        "options": options.model_dump(),
        "execution": execution,
        "history_quality": history_quality,
        "data_lineage": {
            "source_kind": normalized.source_kind,
            "actual_purchase_cost_skus": history_quality[
                "actual_purchase_cost_skus"
            ],
            "actual_inventory_skus": history_quality[
                "actual_inventory_skus"
            ],
            "actual_logistics_skus": history_quality[
                "actual_logistics_skus"
            ],
            "production_data_ready_skus": history_quality[
                "production_data_ready_skus"
            ],
            "sku_count": history_quality["sku_count"],
        },
    }

    if store is not None:
        store.save(payload)

    if output_dir is not None:
        target = Path(output_dir)
        target.mkdir(parents=True, exist_ok=True)

        pd.DataFrame(recommendations if financial_ready else forecast_rows).to_csv(
            target / f"{run_id}_recommendations.csv",
            index=False,
        )
        (target / f"{run_id}_result.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (target / "latest_recommendations.csv").write_bytes(
            (target / f"{run_id}_recommendations.csv").read_bytes()
        )
        (target / "latest_result.json").write_bytes(
            (target / f"{run_id}_result.json").read_bytes()
        )

    return payload


def run_from_frame(
    *,
    frame: pd.DataFrame,
    market: dict[str, Any],
    options: OptimizeOptions,
    store: RunStore | None = None,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    return run_optimization(
        normalized=normalize_history(frame),
        market=market,
        options=options,
        store=store,
        output_dir=output_dir,
    )


def register_outcomes(
    *,
    store: RunStore,
    outcomes: list[dict[str, Any]],
) -> dict[str, Any]:
    evaluations: list[dict[str, Any]] = []
    created_at = datetime.now(timezone.utc).isoformat()
    for outcome in outcomes:
        run_id = str(outcome["run_id"])
        sku = str(outcome["sku"])
        recommendation = store.get_recommendation(run_id, sku)
        if recommendation is None:
            raise ValueError(f"Рекомендация не найдена: run_id={run_id}, sku={sku}.")
        actual_units = float(outcome["actual_units"])
        predicted_units = float(recommendation.get("price_adjusted_demand", 0))
        actual_contribution = actual_units * (
            float(outcome["actual_sell_price"])
            - float(outcome["actual_purchase_cost"])
            - float(outcome.get("actual_shipping_cost", 0))
        )
        evaluation = {
            **outcome,
            "run_id": run_id,
            "sku": sku,
            "created_at": created_at,
            "recommended_sell_price": recommendation["recommended_sell_price"],
            "recommended_purchase_target": recommendation[
                "target_purchase_price"
            ],
            "predicted_units": round(predicted_units, 4),
            "forecast_absolute_percentage_error": round(
                abs(actual_units - predicted_units) / max(1.0, actual_units),
                5,
            ),
            "actual_contribution": round(actual_contribution, 2),
            "model_expected_profit_gain_proxy": round(
                float(recommendation["expected_profit_after"])
                - float(recommendation["expected_profit_before"]),
                2,
            ),
            "causal_uplift_claim": False,
        }
        evaluations.append(evaluation)
    store.save_outcomes(evaluations)
    run_ids = sorted({row["run_id"] for row in evaluations})
    return {
        "saved": len(evaluations),
        "run_ids": run_ids,
        "reports": [build_shadow_report(store=store, run_id=run_id) for run_id in run_ids],
    }


def build_shadow_report(*, store: RunStore, run_id: str) -> dict[str, Any]:
    outcomes = store.list_outcomes(run_id)
    if not outcomes:
        return {
            "run_id": run_id,
            "observations": 0,
            "status": "waiting_for_actuals",
            "message_ru": "Загрузите фактические продажи через /v1/outcomes.",
        }
    return {
        "run_id": run_id,
        "observations": len(outcomes),
        "sku_count": len({row["sku"] for row in outcomes}),
        "actual_contribution": round(
            sum(float(row["actual_contribution"]) for row in outcomes), 2
        ),
        "mean_forecast_ape": round(
            sum(float(row["forecast_absolute_percentage_error"]) for row in outcomes)
            / len(outcomes),
            5,
        ),
        "model_expected_profit_gain_proxy": round(
            sum(float(row["model_expected_profit_gain_proxy"]) for row in outcomes),
            2,
        ),
        "causal_uplift_claim": False,
        "status": "shadow_observed",
        "message_ru": (
            "Отчет сравнивает прогноз с фактом. Ожидаемый прирост — модельный proxy, "
            "а не доказанный причинный эффект; для причинной оценки нужен A/B-тест."
        ),
        "outcomes": outcomes,
    }
