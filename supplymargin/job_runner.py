from __future__ import annotations

import io
import json
import platform
import traceback
from datetime import datetime, timezone

import joblib
import pandas as pd
import sklearn
from sqlalchemy.orm import Session

from supplymargin.config import settings
from supplymargin.data import load_history
from supplymargin.market import load_market_snapshot
from supplymargin.models import DatasetVersion, Run
from supplymargin.object_storage import storage
from supplymargin.schemas import OptimizeOptions
from supplymargin.service import run_optimization


def execute_run(db: Session, run_id: str) -> None:
    run = db.get(Run, run_id)
    if run is None:
        raise ValueError(f"Run not found: {run_id}")
    version = db.get(DatasetVersion, run.dataset_version_id)
    if version is None or version.user_id != run.user_id:
        raise ValueError("Версия таблицы не найдена.")

    run.status = "running"
    run.started_at = datetime.now(timezone.utc)
    run.error_message = None
    run.summary_json = {
        "_progress": {
            "percent": 1,
            "stage": "queued",
            "message": "Расчёт запущен",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    }
    db.commit()

    prefix = f"users/{run.user_id}/datasets/{run.dataset_id}/runs/{run.id}"
    model_key = f"{prefix}/model.joblib"
    manifest_key = f"{prefix}/model_manifest.json"
    result_key = f"{prefix}/result.json"
    recommendations_key = f"{prefix}/recommendations.xlsx"

    try:
        def report_progress(percent: int, stage: str, message: str) -> None:
            current = db.get(Run, run_id)
            if current is None or current.status != "running":
                return
            current.summary_json = {
                "_progress": {
                    "percent": max(0, min(99, int(percent))),
                    "stage": str(stage),
                    "message": str(message),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            }
            db.commit()

        report_progress(3, "load", "Чтение и проверка файла истории")
        normalized = load_history(storage.get_bytes(version.normalized_key))
        options = OptimizeOptions.model_validate(run.options_json)

        def save_model(model, metadata: dict) -> None:
            buffer = io.BytesIO()
            joblib.dump(model, buffer, compress=3)
            storage.put_bytes(model_key, buffer.getvalue(), "application/octet-stream")
            manifest = {
                "run_id": run.id,
                "user_id": run.user_id,
                "dataset_id": run.dataset_id,
                "dataset_version_id": version.id,
                "dataset_sha256": version.sha256,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "python_version": platform.python_version(),
                "scikit_learn_version": sklearn.__version__,
                **metadata,
            }
            storage.put_bytes(
                manifest_key,
                json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
                "application/json",
            )

        report_progress(5, "prepare", "Таблица загружена; подготовка расчёта")
        payload = run_optimization(
            normalized=normalized,
            market=load_market_snapshot(settings.market_file),
            options=options,
            run_id=run.id,
            model_callback=save_model,
            progress_callback=report_progress,
        )
        report_progress(97, "export", "Сохранение результата и Excel-файла")
        storage.put_bytes(
            result_key,
            json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            "application/json",
        )
        excel = io.BytesIO()
        with pd.ExcelWriter(excel, engine="openpyxl") as writer:
            pd.DataFrame(payload["recommendations"]).to_excel(
                writer, sheet_name="recommendations", index=False
            )
            pd.DataFrame([payload["summary"]]).to_excel(
                writer, sheet_name="summary", index=False
            )
            pd.DataFrame(payload.get("warnings", []), columns=["warning"]).to_excel(
                writer, sheet_name="warnings", index=False
            )
        storage.put_bytes(
            recommendations_key,
            excel.getvalue(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        run = db.get(Run, run_id)
        assert run is not None
        run.status = "completed"
        run.summary_json = payload["summary"]
        run.result_key = result_key
        run.recommendations_key = recommendations_key
        run.model_key = model_key
        run.model_manifest_key = manifest_key
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:
        db.rollback()
        failed = db.get(Run, run_id)
        if failed is not None:
            failed.status = "failed"
            failed.error_message = f"{type(exc).__name__}: {exc}"[:4000]
            failed.finished_at = datetime.now(timezone.utc)
            db.commit()
        traceback.print_exc()
