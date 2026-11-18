from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.metrics import r2_score


@dataclass(slots=True)
class ElasticityEstimate:
    sku: str
    elasticity: float
    confidence: str
    observations: int
    price_points: int
    r2: float | None
    source: str


def _fit_one(group: pd.DataFrame) -> tuple[float, float] | None:
    valid = group[(group["units"] >= 0) & (group["sell_price"] > 0)].copy()
    unique_prices = valid["sell_price"].round(4).nunique()
    if len(valid) < 8 or unique_prices < 3:
        return None
    competitor = valid["competitor_price"].where(
        valid["competitor_price"] > 0, valid["sell_price"]
    )
    relative_price = np.log(valid["sell_price"] / competitor)
    trend = np.linspace(0.0, 1.0, len(valid))
    angle = 2 * np.pi * valid["date"].dt.month.to_numpy() / 12.0
    x = np.column_stack([relative_price, trend, np.sin(angle), np.cos(angle)])
    y = np.log1p(valid["units"].to_numpy(dtype=float))
    model = Ridge(alpha=2.0)
    model.fit(x, y)
    prediction = model.predict(x)
    return float(model.coef_[0]), float(r2_score(y, prediction))


def estimate_elasticities(
    df: pd.DataFrame,
    *,
    fallback_elasticity: float = -1.25,
) -> dict[str, ElasticityEstimate]:
    direct: dict[str, tuple[float, float]] = {}
    for sku, group in df.groupby("sku"):
        fitted = _fit_one(group.sort_values("date"))
        if fitted is None:
            continue
        coefficient, r2 = fitted
        # Positive observational coefficients usually reflect promotions,
        # selection or endogeneity, not a trustworthy upward demand curve.
        if coefficient < -0.05:
            direct[str(sku)] = (float(np.clip(coefficient, -4.0, -0.1)), r2)

    category_pool: dict[str, float] = {}
    sku_category = (
        df.sort_values("date").groupby("sku")["category"].last().astype(str).to_dict()
    )
    for category in sorted(set(sku_category.values())):
        values = [
            elasticity
            for sku, (elasticity, _) in direct.items()
            if sku_category.get(sku) == category
        ]
        if values:
            category_pool[category] = float(np.median(values))

    result: dict[str, ElasticityEstimate] = {}
    for sku, group in df.groupby("sku"):
        sku = str(sku)
        observations = len(group)
        price_points = int(group["sell_price"].round(4).nunique())
        if sku in direct:
            elasticity, r2 = direct[sku]
            confidence = "high" if observations >= 16 and r2 >= 0.35 else "medium"
            result[sku] = ElasticityEstimate(
                sku=sku,
                elasticity=elasticity,
                confidence=confidence,
                observations=observations,
                price_points=price_points,
                r2=r2,
                source="sku_ridge",
            )
            continue

        category = str(group["category"].iloc[-1])
        category_value = category_pool.get(category)
        result[sku] = ElasticityEstimate(
            sku=sku,
            elasticity=category_value if category_value is not None else fallback_elasticity,
            confidence="low",
            observations=observations,
            price_points=price_points,
            r2=None,
            source="category_fallback" if category_value is not None else "global_fallback",
        )
    return result

