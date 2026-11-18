from __future__ import annotations

from typing import Any

from supplymargin.config import Settings

BLOCKING_FLAGS = {
    "price_floor_above_change_guardrail",
    "supplier_price_above_walk_away",
}


def apply_execution_policy(
    *,
    recommendations: list[dict[str, Any]],
    validation_wape: float,
    history_quality: dict[str, Any],
    settings: Settings,
) -> dict[str, Any]:
    quality_by_sku = {
        row["sku"]: row for row in history_quality.get("per_sku", [])
    }
    approval_count = 0
    auto_count = 0
    for row in recommendations:
        quality = quality_by_sku.get(row["sku"], {})
        current = max(0.01, float(row["current_sell_price"]))
        price_change = (
            float(row["recommended_sell_price"]) - current
        ) / current
        approval_reasons: list[str] = []
        auto_reasons: list[str] = []

        if validation_wape > settings.approval_wape_max:
            approval_reasons.append("portfolio_wape_above_approval_limit")
        if not quality.get("eligible_history", False):
            approval_reasons.append("insufficient_sku_history")
        if not quality.get("purchase_cost_is_actual", False):
            approval_reasons.append("purchase_cost_not_actual")
        if not quality.get("inventory_is_actual", False):
            approval_reasons.append("daily_inventory_not_actual")
        if not quality.get("logistics_cost_is_actual", False):
            approval_reasons.append("logistics_cost_not_actual")
        if BLOCKING_FLAGS.intersection(row.get("flags", [])):
            approval_reasons.append("blocking_economic_guardrail")
        eligible_approval = not approval_reasons

        if not eligible_approval:
            auto_reasons.extend(approval_reasons)
        if validation_wape > settings.auto_wape_max:
            auto_reasons.append("portfolio_wape_above_auto_limit")
        if row.get("elasticity_confidence") == "low":
            auto_reasons.append("low_elasticity_confidence")
        if abs(price_change) > settings.max_auto_price_change:
            auto_reasons.append("price_change_above_auto_limit")
        if float(row.get("stress_profit_proxy", 0)) < 0:
            auto_reasons.append("negative_stress_profit")
        eligible_auto = not auto_reasons

        if settings.execution_mode == "shadow":
            status = "shadow_observation"
        elif settings.execution_mode == "approval":
            status = "pending_approval" if eligible_approval else "blocked_quality"
        elif not settings.auto_publish_enabled:
            status = "auto_kill_switch"
        else:
            status = "auto_ready" if eligible_auto else "blocked_quality"

        row["execution_mode"] = settings.execution_mode
        row["workflow_status"] = status
        row["quality_gate_status"] = (
            "passed" if eligible_approval else "blocked_for_approval"
        )
        row["published"] = False
        row["price_change_percent"] = round(price_change * 100, 2)
        row["eligible_for_approval"] = eligible_approval
        row["eligible_for_auto"] = eligible_auto
        row["approval_gate_reasons"] = approval_reasons
        row["auto_gate_reasons"] = sorted(set(auto_reasons))
        approval_count += int(eligible_approval)
        auto_count += int(eligible_auto)

    return {
        "mode": settings.execution_mode,
        "published": False,
        "publication_adapter_configured": False,
        "auto_publish_kill_switch": not settings.auto_publish_enabled,
        "approval_eligible_skus": approval_count,
        "auto_eligible_skus": auto_count,
        "blocked_skus": len(recommendations) - approval_count,
        "quality_gate_explanation_ru": (
            "В shadow-mode все рекомендации только наблюдаются. Поле "
            "quality_gate_status отдельно показывает, прошла ли рекомендация "
            "требования для будущего режима approval."
        ),
        "message_ru": (
            "Цены не публикуются: shadow-mode сохраняет рекомендации для "
            "последующего сравнения с фактом."
            if settings.execution_mode == "shadow"
            else "Публикация в master-систему не выполняется без отдельного адаптера."
        ),
    }
