from __future__ import annotations

import io
import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from supplymargin.auth import get_current_user
from supplymargin.database import get_db
from supplymargin.models import Dataset, DatasetVersion, Outcome, Run, User
from supplymargin.object_storage import storage
from supplymargin.plans import get_plan
from supplymargin.schemas import OutcomeRequest, RunCreateRequest
from supplymargin.subscriptions import get_subscription, require_active_subscription

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/runs", tags=["runs"])


def _run_for_user(db: Session, user_id: str, run_id: str) -> Run:
    run = db.scalar(select(Run).where(Run.id == run_id, Run.user_id == user_id))
    if run is None:
        raise HTTPException(status_code=404, detail="Расчёт не найден.")
    return run


def _payload(run: Run, dataset_name: str | None = None) -> dict:
    progress = None
    if run.status in {"queued", "running"}:
        stored = run.summary_json if isinstance(run.summary_json, dict) else {}
        progress = stored.get("_progress")
        if progress is None and run.status == "queued":
            progress = {
                "percent": 0,
                "stage": "queued",
                "message": "Ожидание свободного обработчика",
            }
    return {
        "id": run.id,
        "dataset_id": run.dataset_id,
        "dataset_version_id": run.dataset_version_id,
        "dataset_name": dataset_name,
        "status": run.status,
        "options": run.options_json,
        "summary": run.summary_json if run.status == "completed" else None,
        "progress": progress,
        "error": run.error_message,
        "created_at": run.created_at.isoformat(),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "downloads": {
            "recommendations": f"/v1/runs/{run.id}/download"
            if run.recommendations_key
            else None,
            "result": f"/v1/runs/{run.id}/result" if run.result_key else None,
        },
    }


@router.post("", status_code=202)
def create_run(
    body: RunCreateRequest,
    user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
) -> dict:
    version = db.scalar(
        select(DatasetVersion).where(
            DatasetVersion.id == body.dataset_version_id,
            DatasetVersion.user_id == user.id,
        )
    )
    if version is None:
        raise HTTPException(status_code=404, detail="Версия таблицы не найдена.")

    subscription = get_subscription(db, user.id)
    if subscription is None:
        raise HTTPException(status_code=403, detail="Подписка не найдена.")

    plan = get_plan(subscription.plan_code)
    now = datetime.now(timezone.utc)
    month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)

    used = db.scalar(
        select(func.count(Run.id)).where(
            Run.user_id == user.id,
            Run.created_at >= month_start,
            Run.status.in_(["queued", "running", "completed"]),
        )
    )
    if int(used or 0) >= plan.monthly_runs:
        raise HTTPException(status_code=429, detail="Месячный лимит расчётов исчерпан.")

    active = db.scalar(
        select(func.count(Run.id)).where(
            Run.user_id == user.id,
            Run.status.in_(["queued", "running"]),
        )
    )
    active_limit = 1 if plan.code == "basic" else 3
    if int(active or 0) >= active_limit:
        raise HTTPException(
            status_code=429,
            detail="Дождитесь завершения текущего расчёта.",
        )

    run = Run(
        id=str(uuid.uuid4()),
        user_id=user.id,
        dataset_id=version.dataset_id,
        dataset_version_id=version.id,
        status="queued",
        options_json=body.options.model_dump(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return _payload(run)


@router.get("")
def list_runs(
    limit: int = 50,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    limit = max(1, min(100, limit))
    rows = db.execute(
        select(Run, Dataset.name)
        .join(Dataset, Dataset.id == Run.dataset_id)
        .where(Run.user_id == user.id)
        .order_by(Run.created_at.desc())
        .limit(limit)
    ).all()
    return [_payload(run, name) for run, name in rows]


@router.get("/{run_id}")
def get_run(
    run_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    run = _run_for_user(db, user.id, run_id)
    dataset = db.get(Dataset, run.dataset_id)
    return _payload(run, dataset.name if dataset else None)


@router.delete("/{run_id}", status_code=204)
def delete_run(
    run_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    run = _run_for_user(db, user.id, run_id)

    # Не удаляем расчёт, пока он выполняется.
    if run.status in {"queued", "running"}:
        raise HTTPException(
            status_code=409,
            detail="Нельзя удалить расчёт, пока он выполняется.",
        )

    storage_keys = [
        run.result_key,
        run.recommendations_key,
        run.model_key,
        run.model_manifest_key,
    ]

    # У Outcome может не быть каскадного удаления по внешнему ключу.
    db.execute(
        delete(Outcome).where(
            Outcome.run_id == run.id,
            Outcome.user_id == user.id,
        )
    )
    db.delete(run)
    db.commit()

    # Файлы чистим после успешного commit: ошибка хранилища не должна
    # возвращать удалённую запись в базу. Ошибку записываем в лог.
    for key in storage_keys:
        if key:
            try:
                storage.delete(key)
            except Exception:
                logger.exception("Не удалось удалить файл расчёта %s", key)


@router.get("/{run_id}/result")
def get_result(
    run_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    run = _run_for_user(db, user.id, run_id)
    if run.status != "completed" or not run.result_key:
        raise HTTPException(status_code=409, detail="Результат ещё не готов.")
    return json.loads(storage.get_bytes(run.result_key))


@router.get("/{run_id}/download")
def download_recommendations(
    run_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    run = _run_for_user(db, user.id, run_id)
    if run.status != "completed" or not run.recommendations_key:
        raise HTTPException(status_code=409, detail="Файл ещё не готов.")

    return StreamingResponse(
        io.BytesIO(storage.get_bytes(run.recommendations_key)),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="supplymargin_{run.id}.xlsx"'
        },
    )


@router.post("/{run_id}/retry", status_code=202)
def retry_run(
    run_id: str,
    user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
) -> dict:
    run = _run_for_user(db, user.id, run_id)
    if run.status != "failed":
        raise HTTPException(
            status_code=409,
            detail="Повторить можно только ошибочный расчёт.",
        )

    run.status = "queued"
    run.error_message = None
    run.started_at = None
    run.finished_at = None
    db.commit()
    db.refresh(run)
    return _payload(run)


@router.post("/{run_id}/outcomes")
def save_run_outcomes(
    run_id: str,
    body: OutcomeRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    run = _run_for_user(db, user.id, run_id)
    if run.status != "completed" or not run.result_key:
        raise HTTPException(status_code=409, detail="Расчёт ещё не завершён.")

    result = json.loads(storage.get_bytes(run.result_key))
    recommendations = {
        str(item["sku"]): item for item in result.get("recommendations", [])
    }

    evaluations: list[dict] = []
    for item in body.outcomes:
        if item.run_id != run_id:
            raise HTTPException(status_code=422, detail="run_id в строке не совпадает.")

        recommendation = recommendations.get(str(item.sku))
        if recommendation is None:
            raise HTTPException(
                status_code=422,
                detail=f"SKU {item.sku} отсутствует в рекомендациях.",
            )

        actual_units = float(item.actual_units)
        predicted_units = float(recommendation.get("price_adjusted_demand", 0))
        actual_contribution = actual_units * (
            float(item.actual_sell_price)
            - float(item.actual_purchase_cost)
            - float(item.actual_shipping_cost)
        )

        evaluation = {
            **item.model_dump(),
            "predicted_units": round(predicted_units, 4),
            "recommended_sell_price": recommendation.get("recommended_sell_price"),
            "forecast_absolute_percentage_error": round(
                abs(actual_units - predicted_units) / max(1.0, actual_units), 5
            ),
            "actual_contribution": round(actual_contribution, 2),
            "causal_uplift_claim": False,
        }

        existing = db.scalar(
            select(Outcome).where(
                Outcome.run_id == run_id,
                Outcome.user_id == user.id,
                Outcome.sku == str(item.sku),
                Outcome.actual_date == item.actual_date,
            )
        )
        if existing is None:
            db.add(
                Outcome(
                    id=str(uuid.uuid4()),
                    user_id=user.id,
                    run_id=run_id,
                    sku=str(item.sku),
                    actual_date=item.actual_date,
                    evaluation_json=evaluation,
                )
            )
        else:
            existing.evaluation_json = evaluation

        evaluations.append(evaluation)

    db.commit()
    mean_ape = (
        sum(row["forecast_absolute_percentage_error"] for row in evaluations)
        / len(evaluations)
        if evaluations
        else 0.0
    )
    return {
        "saved": len(evaluations),
        "mean_forecast_ape": round(mean_ape, 5),
        "causal_uplift_claim": False,
        "outcomes": evaluations,
    }


@router.get("/{run_id}/outcomes")
def list_run_outcomes(
    run_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    _run_for_user(db, user.id, run_id)
    rows = db.scalars(
        select(Outcome)
        .where(Outcome.run_id == run_id, Outcome.user_id == user.id)
        .order_by(Outcome.actual_date, Outcome.sku)
    ).all()
    return [item.evaluation_json for item in rows]
