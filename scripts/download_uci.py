"""Download and adapt the official UCI Online Retail dataset.

The source contains real transactions but no private procurement cost or stock.
Derived demo fields are explicitly marked. Do not treat them as accounting data.

Citation:
Chen, D. (2015). Online Retail [Dataset]. UCI Machine Learning Repository.
https://doi.org/10.24432/C5BW33 (CC BY 4.0).
"""

from __future__ import annotations

import argparse
import io
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

UCI_URL = "https://archive.ics.uci.edu/static/public/352/online%2Bretail.zip"


def download(url: str = UCI_URL) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "SupplyMarginAI/1.0 UCI importer"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def build_canonical(blob: bytes, top_skus: int) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        xlsx_name = next(name for name in archive.namelist() if name.endswith(".xlsx"))
        with archive.open(xlsx_name) as source:
            raw = pd.read_excel(source)
    raw.columns = [str(c).strip() for c in raw.columns]
    raw["InvoiceDate"] = pd.to_datetime(raw["InvoiceDate"], errors="coerce")
    raw["Quantity"] = pd.to_numeric(raw["Quantity"], errors="coerce")
    raw["UnitPrice"] = pd.to_numeric(raw["UnitPrice"], errors="coerce")
    raw = raw[
        raw["InvoiceDate"].notna()
        & (raw["Quantity"] > 0)
        & (raw["UnitPrice"] > 0)
        & ~raw["InvoiceNo"].astype(str).str.startswith("C")
    ].copy()
    keep = (
        raw.groupby("StockCode")["Quantity"]
        .sum()
        .nlargest(top_skus)
        .index.astype(str)
    )
    raw["StockCode"] = raw["StockCode"].astype(str)
    raw = raw[raw["StockCode"].isin(keep)]
    raw["date"] = raw["InvoiceDate"].dt.to_period("W").dt.start_time
    grouped = (
        raw.groupby(["StockCode", "Description", "date"], as_index=False)
        .agg(units=("Quantity", "sum"), sell_price=("UnitPrice", "median"))
        .rename(columns={"StockCode": "sku", "Description": "category"})
    )
    grouped["purchase_cost"] = grouped["sell_price"] * 0.58
    grouped["shipping_cost"] = np.maximum(0.05, grouped["sell_price"] * 0.04)
    grouped["handling_cost"] = np.maximum(0.02, grouped["sell_price"] * 0.01)
    grouped["storage_cost_per_unit_day"] = grouped["purchase_cost"] * 0.0007
    grouped["stock"] = np.maximum(5, grouped["units"] * 1.5)
    grouped["on_order"] = 0
    grouped["lead_time_days"] = 14
    grouped["competitor_price"] = np.nan
    grouped["purchase_cost_source"] = "estimated"
    grouped["stock_source"] = "estimated"
    grouped["shipping_cost_source"] = "estimated"
    grouped["handling_cost_source"] = "estimated"
    grouped["storage_cost_source"] = "estimated"
    return grouped


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/demo/uci_online_retail_top100.csv")
    parser.add_argument("--top-skus", type=int, default=100)
    args = parser.parse_args()
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    frame = build_canonical(download(), max(1, args.top_skus))
    frame.to_csv(target, index=False)
    print(f"Saved {len(frame):,} rows / {frame['sku'].nunique()} SKU to {target}")


if __name__ == "__main__":
    main()
