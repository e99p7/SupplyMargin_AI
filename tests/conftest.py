from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture()
def market_snapshot() -> dict:
    path = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "demo"
        / "market_snapshot_2026-07-25.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))

