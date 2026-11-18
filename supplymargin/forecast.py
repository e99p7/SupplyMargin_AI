from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error

FEATURES = [
    "sku_code",
    "category_code",
    "period_index",
    "month_sin",
    "month_cos",
    "lag_1",
    "lag_2",
    "rolling_3",
]


@dataclass(slots=True)
class ForecastResult:
    sku: str
    mean: float
    p10: float
    p90: float
    horizon: int
    period_days: int
    path: list[float]


@dataclass(slots=True)
class PersistenceBaseline:
    """Baseline for datasets without enough lagged observations to train a model."""

    def predict(self, features: np.ndarray) -> np.ndarray:
        # FEATURES stores lag_1 at index 5. The baseline carries the latest
        # observed demand forward and deliberately does not claim a learned trend.
        values = np.asarray(features, dtype=float)[:, FEATURES.index("lag_1")]
        return np.log1p(np.clip(values, 0, None))


@dataclass(slots=True)
class FittedDemandModel:
    model: Any
    sku_codes: dict[str, int]
    category_codes: dict[str, int]
    residual_std: dict[str, float]
    validation_wape: float
    validation_mae: float
    training_rows: int
    period_days: int
    fallback_scale: float
    forecast_method: str = "global_gradient_boosting"
    validation_available: bool = True

    def _future_feature(
        self,
        *,
        sku: str,
        category: str,
        period_index: int,
        future_date: pd.Timestamp,
        history: list[float],
    ) -> np.ndarray:
        month_angle = 2 * np.pi * future_date.month / 12.0
        lag_1 = history[-1]
        lag_2 = history[-2] if len(history) > 1 else lag_1
        rolling_3 = float(np.mean(history[-3:]))
        return np.asarray(
            [
                self.sku_codes.get(sku, -1),
                self.category_codes.get(category, -1),
                period_index,
                np.sin(month_angle),
                np.cos(month_angle),
                lag_1,
                lag_2,
                rolling_3,
            ],
            dtype=float,
        )

    def forecast(
        self,
        history_df: pd.DataFrame,
        *,
        horizon: int,
        demand_factor: float = 1.0,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> list[ForecastResult]:
        """Forecast all SKUs in batches rather than calling sklearn per SKU-step."""
        history_df = _prepare_demand_history(history_df)
        if horizon < 1:
            raise ValueError("Горизонт прогноза должен быть не меньше 1.")

        states: dict[str, dict[str, Any]] = {}
        for sku, group in history_df.groupby("sku", sort=True):
            group = group.sort_values("date")
            units = group["units"].astype(float).tolist()
            if not units:
                continue
            states[str(sku)] = {
                "category": str(group["category"].iloc[-1]),
                "latest_date": pd.Timestamp(group["date"].max()),
                "period_index": len(group),
                "recursive": units.copy(),
                "cap": max(1.0, float(np.quantile(units, 0.9)) * 3.0),
                "predictions": [],
            }

        skus = list(states)
        total_skus = len(skus)
        # Chunking limits memory for large portfolios while still batching model
        # inference; previously a DataFrame and sklearn predict call were made
        # for every individual SKU and every horizon step.
        batch_size = 8192
        for step in range(1, horizon + 1):
            for chunk_start in range(0, total_skus, batch_size):
                chunk_skus = skus[chunk_start:chunk_start + batch_size]
                features: list[np.ndarray] = []
                for sku in chunk_skus:
                    state = states[sku]
                    future_date = pd.Timestamp(
                        state["latest_date"].to_pydatetime()
                        + timedelta(days=int(self.period_days * step))
                    )
                    features.append(
                        self._future_feature(
                            sku=sku,
                            category=state["category"],
                            period_index=state["period_index"] + step,
                            future_date=future_date,
                            history=state["recursive"],
                        )
                    )
                future_frame = pd.DataFrame(features, columns=FEATURES)
                predictions = np.expm1(self.model.predict(future_frame))
                for sku, predicted in zip(chunk_skus, predictions):
                    state = states[sku]
                    if not np.isfinite(predicted):
                        predicted = float(np.mean(state["recursive"][-3:]))
                    predicted = float(
                        np.clip(predicted * demand_factor, 0.0, state["cap"])
                    )
                    state["predictions"].append(predicted)
                    state["recursive"].append(predicted)
            if progress_callback is not None:
                progress_callback(step, horizon)

        results: list[ForecastResult] = []
        for sku in skus:
            state = states[sku]
            predictions = state["predictions"]
            total = float(sum(predictions))
            sigma_per_period = self.residual_std.get(sku, self.fallback_scale)
            total_sigma = float(sigma_per_period * np.sqrt(horizon))
            results.append(
                ForecastResult(
                    sku=sku,
                    mean=total,
                    p10=max(0.0, total - 1.2816 * total_sigma),
                    p90=max(total, total + 1.2816 * total_sigma),
                    horizon=horizon,
                    period_days=self.period_days,
                    path=[round(x, 4) for x in predictions],
                )
            )
        return results

def _prepare_demand_history(df: pd.DataFrame) -> pd.DataFrame:
    """Prepare sales history using only SKU, date and units."""
    required = {"sku", "date", "units"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise ValueError(
            "Для прогноза спроса необходимы колонки sku, date, units. "
            f"Не хватает: {missing}."
        )

    work = df.copy()
    work["sku"] = work["sku"].fillna("").astype(str).str.strip()
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    work["units"] = pd.to_numeric(work["units"], errors="coerce")

    before = len(work)
    work = work.dropna(subset=["date", "units"])
    work = work[(work["sku"] != "") & (work["units"] >= 0)].copy()

    if work.empty:
        raise ValueError(
            "Нет корректных наблюдений спроса: проверьте SKU, даты и units."
        )

    # Отсутствующая категория не должна блокировать прогноз.
    if "category" not in work.columns:
        work["category"] = "unknown"
    else:
        work["category"] = (
            work["category"].fillna("unknown").astype(str).str.strip()
        )
        work.loc[work["category"] == "", "category"] = "unknown"

    # Если за один день есть несколько записей по SKU,
    # объединяем их в дневной объём продаж.
    work = (
        work.groupby(["sku", "date"], as_index=False, sort=True)
        .agg(
            units=("units", "sum"),
            category=("category", "last"),
        )
        .sort_values(["sku", "date"])
        .reset_index(drop=True)
    )

    if before > len(work):
        # Это может быть как очистка строк, так и объединение дат.
        # Количество строк не используется для оценки качества данных.
        pass

    return work


def _infer_period_days(df: pd.DataFrame) -> int:
    work = _prepare_demand_history(df)
    deltas: list[float] = []

    for _, group in work.groupby("sku"):
        dates = group["date"].drop_duplicates().sort_values()
        values = dates.diff().dt.days.dropna()
        deltas.extend(values[values > 0].tolist())

    if not deltas:
        return 1

    median = int(round(float(np.median(deltas))))
    if 25 <= median <= 35:
        return 30
    if 5 <= median <= 9:
        return 7
    return max(1, median)



def _training_table(
    df: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, int], dict[str, int]]:
    work = _prepare_demand_history(df)

    sku_codes = {
        value: idx
        for idx, value in enumerate(sorted(work["sku"].unique()))
    }
    category_codes = {
        value: idx
        for idx, value in enumerate(sorted(work["category"].unique()))
    }

    work["sku_code"] = work["sku"].map(sku_codes).astype(float)
    work["category_code"] = (
        work["category"].map(category_codes).astype(float)
    )
    work["period_index"] = work.groupby("sku").cumcount().astype(float)

    angle = 2 * np.pi * work["date"].dt.month / 12.0
    work["month_sin"] = np.sin(angle)
    work["month_cos"] = np.cos(angle)

    grouped = work.groupby("sku", sort=False)["units"]
    work["lag_1"] = grouped.shift(1)
    work["lag_2"] = grouped.shift(2)
    # Vectorized grouped rolling avoids executing a Python lambda once per SKU,
    # which becomes a major bottleneck for large catalogs.
    shifted = grouped.shift(1)
    rolling = shifted.groupby(work["sku"], sort=False).rolling(
        3, min_periods=1
    ).mean()
    work["rolling_3"] = (
        rolling.reset_index(level=0, drop=True).sort_index().reindex(work.index)
    )

    return (
        work.dropna(subset=["lag_1"]).copy(),
        sku_codes,
        category_codes,
    )

def fit_demand_model(df: pd.DataFrame, random_seed: int = 42) -> FittedDemandModel:
    table, sku_codes, category_codes = _training_table(df)
    MIN_GLOBAL_PERIODS = 10

    if len(table) < MIN_GLOBAL_PERIODS:
        # A snapshot often contains one observation per SKU. It has no lags,
        # so a learned temporal model cannot be trained. Keep demand-only
        # calculations usable with an explicitly labelled persistence baseline.
        work = _prepare_demand_history(df)
        if work.empty:
            raise ValueError(
                "После очистки не осталось валидных наблюдений спроса. "
                "Проверьте SKU, дату и units."
            )
        latest = work.sort_values("date").groupby("sku", sort=False)["units"].last()
        global_scale = max(
            1.0,
            float(np.nanstd(work["units"].to_numpy(dtype=float))) * 0.25,
        )
        residual_std = {
            str(sku): max(1.0, abs(float(value)) * 0.25)
            for sku, value in latest.items()
        }
        return FittedDemandModel(
            model=PersistenceBaseline(),
            sku_codes=sku_codes,
            category_codes=category_codes,
            residual_std=residual_std,
            validation_wape=1.0,  # placeholder; validation_available=False
            validation_mae=0.0,   # placeholder; validation_available=False
            training_rows=0,
            period_days=_infer_period_days(df),
            fallback_scale=global_scale,
            forecast_method="persistence_baseline",
            validation_available=False,
        )
    table = table.sort_values("date")
    split_at = max(10, int(len(table) * 0.8))
    if split_at >= len(table):
        split_at = len(table) - 1
    train = table.iloc[:split_at]
    valid = table.iloc[split_at:]

    model = HistGradientBoostingRegressor(
        loss="squared_error",
        learning_rate=0.05,
        max_iter=250,
        max_leaf_nodes=31,
        min_samples_leaf=max(5, min(20, len(train) // 20)),
        l2_regularization=1.0,
        random_state=random_seed,
    )
    model.fit(train[FEATURES], np.log1p(train["units"].clip(lower=0)))

    valid_prediction = np.expm1(model.predict(valid[FEATURES]))
    valid_prediction = np.clip(valid_prediction, 0, None)
    actual = valid["units"].to_numpy(dtype=float)
    mae = float(mean_absolute_error(actual, valid_prediction))
    wape = float(np.abs(actual - valid_prediction).sum() / max(1.0, actual.sum()))

    all_prediction = np.clip(np.expm1(model.predict(table[FEATURES])), 0, None)
    residuals = table["units"].to_numpy(dtype=float) - all_prediction
    residual_df = pd.DataFrame({"sku": table["sku"].to_numpy(), "residual": residuals})
    residual_std = (
        residual_df.groupby("sku")["residual"]
        .std(ddof=1)
        .fillna(float(np.std(residuals)))
        .clip(lower=0.25)
        .to_dict()
    )
    fallback_scale = max(0.25, float(np.std(residuals)))

    return FittedDemandModel(
        model=model,
        sku_codes=sku_codes,
        category_codes=category_codes,
        residual_std={str(k): float(v) for k, v in residual_std.items()},
        validation_wape=wape,
        validation_mae=mae,
        training_rows=len(train),
        period_days=_infer_period_days(df),
        fallback_scale=fallback_scale,
    )


def model_metadata(model: FittedDemandModel) -> dict[str, Any]:
    return {
        "model_name": type(model.model).__name__,
        "training_rows": model.training_rows,
        "validation_wape": model.validation_wape,
        "validation_mae": model.validation_mae,
        "period_days": model.period_days,
        "sku_count": len(model.sku_codes),
        "forecast_method": model.forecast_method,
        "validation_available": model.validation_available,
        "validation_note": (
            None if model.validation_available else
            "Метрики качества не рассчитывались: недостаточно временной истории. "
            "Использован базовый прогноз по последнему наблюдению."
        ),
    }
