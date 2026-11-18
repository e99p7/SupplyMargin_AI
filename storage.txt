from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class RunStore:
    def __init__(self, results_dir: str | Path) -> None:
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.results_dir / "supplymargin.sqlite3"
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    as_of TEXT NOT NULL,
                    sku_count INTEGER NOT NULL,
                    summary_json TEXT NOT NULL,
                    result_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS recommendations (
                    run_id TEXT NOT NULL,
                    sku TEXT NOT NULL,
                    workflow_status TEXT NOT NULL,
                    eligible_for_approval INTEGER NOT NULL,
                    eligible_for_auto INTEGER NOT NULL,
                    published INTEGER NOT NULL DEFAULT 0,
                    recommendation_json TEXT NOT NULL,
                    decision TEXT,
                    decision_reason TEXT,
                    override_price REAL,
                    decided_at TEXT,
                    PRIMARY KEY (run_id, sku)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    sku TEXT NOT NULL,
                    actual_date TEXT NOT NULL,
                    actual_sell_price REAL NOT NULL,
                    actual_units REAL NOT NULL,
                    actual_purchase_cost REAL NOT NULL,
                    actual_shipping_cost REAL NOT NULL,
                    stock_end REAL,
                    evaluation_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (run_id, sku, actual_date)
                )
                """
            )

    def ping(self) -> bool:
        try:
            with self._connect() as connection:
                connection.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False

    def save(self, payload: dict[str, Any]) -> None:
        summary = payload["summary"]
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO runs
                    (run_id, created_at, as_of, sku_count, summary_json, result_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    summary["run_id"],
                    summary["created_at"],
                    summary["as_of"],
                    summary["sku_count"],
                    json.dumps(summary, ensure_ascii=False),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            for row in payload.get("recommendations", []):
                connection.execute(
                    """
                    INSERT INTO recommendations (
                        run_id, sku, workflow_status, eligible_for_approval,
                        eligible_for_auto, published, recommendation_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(run_id, sku) DO UPDATE SET
                        workflow_status = excluded.workflow_status,
                        eligible_for_approval = excluded.eligible_for_approval,
                        eligible_for_auto = excluded.eligible_for_auto,
                        published = excluded.published,
                        recommendation_json = excluded.recommendation_json
                    """,
                    (
                        summary["run_id"],
                        str(row["sku"]),
                        row.get("workflow_status", "shadow_observation"),
                        int(bool(row.get("eligible_for_approval"))),
                        int(bool(row.get("eligible_for_auto"))),
                        int(bool(row.get("published"))),
                        json.dumps(row, ensure_ascii=False),
                    ),
                )

    def list(self, limit: int = 20) -> list[dict[str, Any]]:
        limit = max(1, min(100, int(limit)))
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT summary_json FROM runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [json.loads(row["summary_json"]) for row in rows]

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT result_json FROM runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            workflow_rows = connection.execute(
                """
                SELECT sku, workflow_status, decision, decision_reason,
                       override_price, decided_at, published
                FROM recommendations WHERE run_id = ?
                """,
                (run_id,),
            ).fetchall()
        if row is None:
            return None
        payload = json.loads(row["result_json"])
        workflow = {item["sku"]: dict(item) for item in workflow_rows}
        for recommendation in payload.get("recommendations", []):
            state = workflow.get(str(recommendation["sku"]))
            if state is not None:
                recommendation.update(
                    {
                        "workflow_status": state["workflow_status"],
                        "decision": state["decision"],
                        "decision_reason": state["decision_reason"],
                        "override_price": state["override_price"],
                        "decided_at": state["decided_at"],
                        "published": bool(state["published"]),
                    }
                )
        return payload

    def get_recommendation(self, run_id: str, sku: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT recommendation_json, workflow_status, decision,
                       decision_reason, override_price, decided_at, published
                FROM recommendations
                WHERE run_id = ? AND sku = ?
                """,
                (run_id, sku),
            ).fetchone()
        if row is None:
            return None
        result = json.loads(row["recommendation_json"])
        result.update(
            {
                "workflow_status": row["workflow_status"],
                "decision": row["decision"],
                "decision_reason": row["decision_reason"],
                "override_price": row["override_price"],
                "decided_at": row["decided_at"],
                "published": bool(row["published"]),
            }
        )
        return result

    def record_decision(
        self,
        *,
        run_id: str,
        sku: str,
        decision: str,
        reason: str,
        override_price: float | None,
        decided_at: str,
    ) -> dict[str, Any] | None:
        status = {
            "approve": "approved_not_published",
            "reject": "rejected",
            "override": "overridden_not_published",
        }[decision]
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE recommendations
                SET workflow_status = ?, decision = ?, decision_reason = ?,
                    override_price = ?, decided_at = ?, published = 0
                WHERE run_id = ? AND sku = ?
                """,
                (status, decision, reason, override_price, decided_at, run_id, sku),
            )
        if cursor.rowcount == 0:
            return None
        return self.get_recommendation(run_id, sku)

    def save_outcomes(self, evaluations: list[dict[str, Any]]) -> None:
        with self._connect() as connection:
            for row in evaluations:
                connection.execute(
                    """
                    INSERT INTO outcomes (
                        run_id, sku, actual_date, actual_sell_price, actual_units,
                        actual_purchase_cost, actual_shipping_cost, stock_end,
                        evaluation_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(run_id, sku, actual_date) DO UPDATE SET
                        actual_sell_price = excluded.actual_sell_price,
                        actual_units = excluded.actual_units,
                        actual_purchase_cost = excluded.actual_purchase_cost,
                        actual_shipping_cost = excluded.actual_shipping_cost,
                        stock_end = excluded.stock_end,
                        evaluation_json = excluded.evaluation_json,
                        created_at = excluded.created_at
                    """,
                    (
                        row["run_id"],
                        row["sku"],
                        row["actual_date"],
                        row["actual_sell_price"],
                        row["actual_units"],
                        row["actual_purchase_cost"],
                        row["actual_shipping_cost"],
                        row.get("stock_end"),
                        json.dumps(row, ensure_ascii=False),
                        row["created_at"],
                    ),
                )

    def list_outcomes(self, run_id: str | None = None) -> list[dict[str, Any]]:
        with self._connect() as connection:
            if run_id is None:
                rows = connection.execute(
                    "SELECT evaluation_json FROM outcomes ORDER BY actual_date, sku"
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT evaluation_json FROM outcomes
                    WHERE run_id = ? ORDER BY actual_date, sku
                    """,
                    (run_id,),
                ).fetchall()
        return [json.loads(row["evaluation_json"]) for row in rows]
