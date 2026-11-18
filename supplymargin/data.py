from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

CANONICAL_COLUMNS = {
    "sku",
    "date",
    "units",
    "sell_price",
    "purchase_cost",
    "shipping_cost",
    "stock",
}

MAX_XLSX_FILES = 2_000
MAX_XLSX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
MAX_XLSX_COMPRESSION_RATIO = 200


def _validate_xlsx_container(content: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as workbook:
            members = workbook.infolist()
            if not members or len(members) > MAX_XLSX_FILES:
                raise ValueError("Некорректная или слишком сложная структура XLSX.")
            uncompressed = sum(item.file_size for item in members)
            compressed = max(1, sum(item.compress_size for item in members))
            if uncompressed > MAX_XLSX_UNCOMPRESSED_BYTES:
                raise ValueError("Распакованный XLSX превышает безопасный лимит 256 МБ.")
            if uncompressed / compressed > MAX_XLSX_COMPRESSION_RATIO:
                raise ValueError("XLSX имеет подозрительно высокий коэффициент сжатия.")
            if any(item.flag_bits & 0x1 for item in members):
                raise ValueError("Зашифрованные XLSX не поддерживаются.")
            names = {item.filename.lower() for item in members}
            if any(name.endswith("vbaproject.bin") for name in names):
                raise ValueError("XLSX с макросами не поддерживается.")
    except zipfile.BadZipFile as exc:
        raise ValueError("Файл не является корректным XLSX.") from exc

PROVENANCE_VALUES = {"actual", "estimated", "missing"}
PROVENANCE_COLUMNS = (
    "purchase_cost_source",
    "stock_source",
    "shipping_cost_source",
    "handling_cost_source",
    "storage_cost_source",
)


@dataclass(slots=True)
class NormalizedData:
    frame: pd.DataFrame
    warnings: list[str]
    source_kind: str


def read_csv_flexible(source: str | Path | bytes) -> pd.DataFrame:
    if isinstance(source, bytes):
        stream = io.BytesIO(source)
        try:
            return pd.read_csv(stream)
        except UnicodeDecodeError:
            stream.seek(0)
            return pd.read_csv(stream, encoding="cp1251")

    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"CSV не найден: {path}")
    try:
        return pd.read_csv(path)
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="cp1251")


def _numeric(series: pd.Series, *, default: float | None = None) -> pd.Series:
    cleaned = (
        series.astype(str)
        .str.replace("\u00a0", "", regex=False)
        .str.replace(" ", "", regex=False)
        .str.replace(",", ".", regex=False)
    )
    result = pd.to_numeric(cleaned, errors="coerce")
    if default is not None:
        result = result.fillna(default)
    return result.astype(float)


def _boolean(series: pd.Series, *, default: bool = False) -> pd.Series:
    normalized = series.astype(str).str.strip().str.lower()
    result = normalized.map(
        {
            "1": True,
            "true": True,
            "yes": True,
            "да": True,
            "0": False,
            "false": False,
            "no": False,
            "нет": False,
        }
    )
    return result.fillna(default).astype(bool)


def _source_series(
    df: pd.DataFrame,
    *,
    column: str,
    default: str,
) -> pd.Series:
    if column not in df:
        return pd.Series(default, index=df.index, dtype="object")
    source = df[column].where(df[column].notna(), "missing")
    source = source.astype(str).str.strip().str.lower()
    aliases = {
        "fact": "actual",
        "factual": "actual",
        "real": "actual",
        "факт": "actual",
        "фактический": "actual",
        "estimate": "estimated",
        "derived": "estimated",
        "model": "estimated",
        "оценка": "estimated",
        "расчетный": "estimated",
        "none": "missing",
        "null": "missing",
        "нет": "missing",
        "": "missing",
    }
    source = source.replace(aliases)
    return source.where(source.isin(PROVENANCE_VALUES), "missing")


def _normalize_canonical(raw: pd.DataFrame) -> NormalizedData:
    df = raw.copy()
    original_columns = set(df.columns)
    warnings: list[str] = []

    # Для прогноза спроса обязательны только эти три поля.
    for col in ("sku", "date", "units"):
        if col not in df.columns:
            raise ValueError(f"В CSV отсутствует обязательная колонка: {col}")

    df["sku"] = df["sku"].fillna("").astype(str).str.strip()
    df["date"] = pd.to_datetime(df["date"], errors="coerce", dayfirst=False)

    # Отсутствующие значения остаются неизвестными, а не нулевыми.
    optional_numeric = (
        "sell_price",
        "purchase_cost",
        "shipping_cost",
        "handling_cost",
        "storage_cost_per_unit_day",
        "stock",
        "on_order",
        "competitor_price",
        "min_price",
        "max_price",
    )
    for col in optional_numeric:
        if col not in df.columns:
            df[col] = np.nan
        df[col] = _numeric(df[col])

    df["units"] = _numeric(df["units"])

    if "lead_time_days" not in df.columns:
        df["lead_time_days"] = 14.0
    else:
        df["lead_time_days"] = _numeric(df["lead_time_days"], default=14.0)

    if "category" not in df.columns:
        df["category"] = "unknown"
    df["category"] = (
        df["category"].fillna("unknown").astype(str).str.strip()
    )
    df.loc[df["category"] == "", "category"] = "unknown"

    # Учитываем явно переданный признак оценочной себестоимости.
    if (
        "purchase_cost_source" not in df.columns
        and "purchase_cost_is_estimated" in df.columns
    ):
        estimated = _boolean(df["purchase_cost_is_estimated"])
        df["purchase_cost_source"] = np.where(
            estimated, "estimated", "actual"
        )

    # Если источник не указан, actual разрешён только при наличии значения.
    source_fields = {
        "purchase_cost_source": "purchase_cost",
        "stock_source": "stock",
        "shipping_cost_source": "shipping_cost",
        "handling_cost_source": "handling_cost",
        "storage_cost_source": "storage_cost_per_unit_day",
    }

    for source_col, value_col in source_fields.items():
        if source_col not in df.columns:
            df[source_col] = np.where(
                df[value_col].notna(), "actual", "missing"
            )
        else:
            df[source_col] = _source_series(
                df, column=source_col, default="missing"
            )
            # Нельзя маркировать отсутствующее значение как actual.
            missing_value = df[value_col].isna()
            df.loc[missing_value, source_col] = "missing"

    if "stock_history_is_daily" in df.columns:
        df["stock_history_is_daily"] = _boolean(
            df["stock_history_is_daily"]
        )
    else:
        daily_by_sku: dict[str, bool] = {}
        for sku, group in df.groupby("sku", sort=False):
            dates = (
                group["date"]
                .dropna()
                .drop_duplicates()
                .sort_values()
            )
            deltas = dates.diff().dt.days.dropna()
            daily_by_sku[str(sku)] = bool(
                len(dates) >= 2
                and not deltas.empty
                and float(deltas.median()) <= 2
            )
        df["stock_history_is_daily"] = df["sku"].map(
            lambda sku: daily_by_sku.get(str(sku), False)
        )

    df["purchase_cost_is_estimated"] = (
        df["purchase_cost_source"] == "estimated"
    )
    df["stock_is_estimated"] = df["stock_source"] == "estimated"
    df["logistics_cost_is_estimated"] = ~(
        df["shipping_cost_source"].eq("actual")
        & df["handling_cost_source"].eq("actual")
        & df["storage_cost_source"].eq("actual")
    )


    # Очистка истории продаж по обязательным полям.
    before = len(df)

    df = df.dropna(subset=["sku", "date", "units"])
    df = df[
        df["sku"].astype(str).str.strip().ne("")
        & (df["units"] >= 0)
    ].copy()

    dropped = before - len(df)

    if df.empty:
        raise ValueError(
            "После очистки не осталось строк с корректными SKU, датой и продажами. "
            "Проверьте колонки sku, date и units; финансовые поля могут быть пустыми."
        )



    missing_prices = (
        df["sell_price"].isna() | df["purchase_cost"].isna()
    )
    if missing_prices.any():
        warnings.append(
            "Цены продажи или закупки отсутствуют: финансовая оптимизация "
            "недоступна до загрузки фактических данных."
        )

    if df["stock"].isna().any():
        warnings.append(
            "Остатки отсутствуют или известны не для всех строк; "
            "неизвестные остатки не интерпретируются как ноль."
        )

    df["lead_time_days"] = (
        df["lead_time_days"].clip(0, 365).round().astype(int)
    )
    for col in ("competitor_price", "min_price", "max_price"):
        df[col] = df[col].where(df[col] > 0)

    df = df.sort_values(["sku", "date"]).reset_index(drop=True)
    return NormalizedData(df, warnings, "canonical")



def _normalize_legacy_retail(raw: pd.DataFrame) -> NormalizedData:
    """Adapt the real retail-price dataset from the user's earlier GA project.

    Sales, retail/freight/competitor prices and dates remain source data.
    Private procurement cost, inventory and handling are not public and are
    explicitly derived for demonstration only.
    """

    df = pd.DataFrame()
    df["sku"] = raw["product_id"].astype(str)
    df["date"] = pd.to_datetime(raw["month_year"], format="%d-%m-%Y", errors="coerce")
    df["units"] = _numeric(raw["qty"])
    df["sell_price"] = _numeric(raw["unit_price"])
    df["shipping_cost"] = _numeric(raw["freight_price"], default=0.0)
    df["category"] = raw.get("product_category_name", "unknown")

    competitor_cols = [c for c in ("comp_1", "comp_2", "comp_3") if c in raw]
    if competitor_cols:
        competitors = raw[competitor_cols].apply(_numeric)
        df["competitor_price"] = competitors.replace(0, np.nan).median(axis=1)
    else:
        df["competitor_price"] = np.nan

    # Demo-only assumptions are visible in every row and in the returned warning.
    df["purchase_cost"] = df["sell_price"] * 0.58
    df["handling_cost"] = np.maximum(0.25, df["sell_price"] * 0.015)
    df["storage_cost_per_unit_day"] = df["purchase_cost"] * 0.0007
    df["stock"] = np.maximum(5.0, df["units"] * 2.2)
    df["on_order"] = 0.0
    df["lead_time_days"] = 14
    df["min_price"] = np.nan
    df["max_price"] = np.nan
    df["purchase_cost_source"] = "estimated"
    df["stock_source"] = "estimated"
    df["shipping_cost_source"] = "actual"
    df["handling_cost_source"] = "estimated"
    df["storage_cost_source"] = "estimated"

    normalized = _normalize_canonical(df)
    normalized.source_kind = "legacy_real_retail_demo"
    normalized.warnings.insert(
        0,
        "Demo использует реальные продажи, цены и freight, но purchase_cost, "
        "stock, handling и storage производные. Источники помечены построчно; "
        "замените их фактическими ERP/WMS/TMS-данными.",
    )
    return normalized


def normalize_history(raw: pd.DataFrame) -> NormalizedData:
    raw.columns = (
        raw.columns.astype(str)
        .str.replace("\ufeff", "", regex=False)  # UTF-8 BOM в первом заголовке
        .str.strip()
        .str.lower()
    )

    lowered = {
        str(c).strip().lower(): c
        for c in raw.columns
    }
    canonical_rename = {
        original: lower
        for lower, original in lowered.items()
    }
    standardized = raw.rename(columns=canonical_rename)

    # Каноническая история спроса может содержать только три обязательных поля.
    demand_columns = {"sku", "date", "units"}
    if demand_columns.issubset(standardized.columns):
        return _normalize_canonical(standardized)

    legacy_required = {
        "product_id",
        "month_year",
        "qty",
        "unit_price",
        "freight_price",
    }
    if legacy_required.issubset(standardized.columns):
        return _normalize_legacy_retail(standardized)

    missing = sorted(demand_columns - set(standardized.columns))
    raise ValueError(
        "Не распознан формат CSV. Для прогноза спроса необходимы колонки "
        f"sku, date, units. Не хватает: {missing}."
    )



def load_history(source: str | Path | bytes) -> NormalizedData:
    return normalize_history(read_csv_flexible(source))


def load_uploaded_history(content: bytes, filename: str) -> NormalizedData:
    """Load a user upload without trusting its MIME type.

    Macro-enabled workbooks and legacy binary Excel files are intentionally
    rejected. The original file name is used only to choose the parser.
    """

    suffix = Path(filename or "").suffix.lower()
    if suffix == ".csv":
        return load_history(content)
    if suffix == ".xlsx":
        _validate_xlsx_container(content)
        try:
            frame = pd.read_excel(io.BytesIO(content), engine="openpyxl")
        except Exception as exc:  # parser exceptions vary by openpyxl version
            raise ValueError(f"Не удалось прочитать XLSX: {exc}") from exc
        return normalize_history(frame)
    if suffix in {".xlsm", ".xls", ".xltm", ".xltx"}:
        raise ValueError(
            "Формат Excel с макросами или устаревший XLS запрещен. "
            "Сохраните файл как обычный XLSX."
        )
    raise ValueError("Поддерживаются только файлы CSV и XLSX.")
