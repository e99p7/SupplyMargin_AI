from __future__ import annotations

import hashlib
import io
import re
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from supplymargin.auth import get_current_user
from supplymargin.data import load_uploaded_history
from supplymargin.database import get_db
from supplymargin.models import Dataset, DatasetVersion, User
from supplymargin.object_storage import storage
from supplymargin.plans import get_plan
from supplymargin.subscriptions import (
    get_subscription,
    require_active_subscription,
)

router = APIRouter(prefix="/v1/datasets", tags=["datasets"])


def _version_payload(version: DatasetVersion) -> dict:
    return {
        "id": version.id,
        "dataset_id": version.dataset_id,
        "version": version.version,
        "filename": version.original_filename,
        "size_bytes": version.size_bytes,
        "sha256": version.sha256,
        "row_count": version.row_count,
        "status": version.status,
        "warnings": version.warnings_json or [],
        "created_at": version.created_at.isoformat(),
    }


def _dataset_for_user(db: Session, user_id: str, dataset_id: str) -> Dataset:
    dataset = db.scalar(
        select(Dataset).where(Dataset.id == dataset_id, Dataset.user_id == user_id)
    )
    if dataset is None:
        raise HTTPException(status_code=404, detail="Таблица не найдена.")
    return dataset


def _safe_filename(filename: str | None) -> str:
    value = Path(filename or "upload.csv").name
    value = re.sub(r'[\x00-\x1f\x7f"\\]', "_", value)
    return value[:255] or "upload.csv"


def _create_version(
    *,
    db: Session,
    user: User,
    dataset: Dataset,
    filename: str,
    content_type: str,
    content: bytes,
) -> DatasetVersion:
    subscription = get_subscription(db, user.id)
    if subscription is None:
        raise HTTPException(status_code=403, detail="Подписка не найдена.")
    plan = get_plan(subscription.plan_code)
    if len(content) > plan.max_upload_mb * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"Максимальный размер файла на тарифе — {plan.max_upload_mb} МБ.",
        )
    try:
        normalized = load_uploaded_history(content, filename)
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    latest = db.scalar(
        select(func.max(DatasetVersion.version)).where(
            DatasetVersion.dataset_id == dataset.id
        )
    )
    version_number = int(latest or 0) + 1
    version_id = str(uuid.uuid4())
    prefix = (
        f"users/{user.id}/datasets/{dataset.id}/"
        f"v{version_number:03d}_{version_id}"
    )
    source_key = f"{prefix}/source{Path(filename).suffix.lower()}"
    normalized_key = f"{prefix}/normalized.csv"
    try:
        storage.put_bytes(source_key, content, content_type)
        storage.put_bytes(
            normalized_key,
            normalized.frame.to_csv(index=False).encode("utf-8"),
            "text/csv; charset=utf-8",
        )
        version = DatasetVersion(
            id=version_id,
            dataset_id=dataset.id,
            user_id=user.id,
            version=version_number,
            original_filename=filename,
            content_type=content_type,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            source_key=source_key,
            normalized_key=normalized_key,
            row_count=len(normalized.frame),
            status="ready",
            warnings_json=list(normalized.warnings),
        )
        db.add(version)
        db.commit()
        db.refresh(version)
        return version
    except Exception:
        db.rollback()
        storage.delete_prefix(prefix)
        raise


@router.post("", status_code=201)
async def create_dataset(
    file: Annotated[UploadFile, File(description="CSV or XLSX history")],
    name: Annotated[str | None, Form(max_length=200)] = None,
    user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
) -> dict:
    subscription = get_subscription(db, user.id)
    assert subscription is not None
    plan = get_plan(subscription.plan_code)
    dataset_count = db.scalar(
        select(func.count(Dataset.id)).where(Dataset.user_id == user.id)
    )
    if int(dataset_count or 0) >= plan.max_datasets:
        raise HTTPException(status_code=429, detail="Лимит таблиц на тарифе исчерпан.")
    filename = _safe_filename(file.filename)
    content = await file.read(plan.max_upload_mb * 1024 * 1024 + 1)
    dataset = Dataset(
        id=str(uuid.uuid4()),
        user_id=user.id,
        name=(name or Path(filename).stem or "Новая таблица")[:200],
    )
    db.add(dataset)
    db.commit()
    db.refresh(dataset)
    try:
        version = _create_version(
            db=db,
            user=user,
            dataset=dataset,
            filename=filename,
            content_type=file.content_type or "application/octet-stream",
            content=content,
        )
    except Exception:
        db.delete(dataset)
        db.commit()
        storage.delete_prefix(f"users/{user.id}/datasets/{dataset.id}")
        raise
    return {
        "id": dataset.id,
        "name": dataset.name,
        "status": dataset.status,
        "created_at": dataset.created_at.isoformat(),
        "latest_version": _version_payload(version),
    }


@router.get("")
def list_datasets(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.scalars(
        select(Dataset)
        .where(Dataset.user_id == user.id)
        .order_by(Dataset.created_at.desc())
    ).all()
    result: list[dict] = []
    for dataset in rows:
        latest = db.scalar(
            select(DatasetVersion)
            .where(DatasetVersion.dataset_id == dataset.id)
            .order_by(DatasetVersion.version.desc())
            .limit(1)
        )
        result.append(
            {
                "id": dataset.id,
                "name": dataset.name,
                "status": dataset.status,
                "created_at": dataset.created_at.isoformat(),
                "latest_version": _version_payload(latest) if latest else None,
            }
        )
    return result


@router.get("/{dataset_id}")
def get_dataset(
    dataset_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    dataset = _dataset_for_user(db, user.id, dataset_id)
    versions = db.scalars(
        select(DatasetVersion)
        .where(
            DatasetVersion.dataset_id == dataset.id,
            DatasetVersion.user_id == user.id,
        )
        .order_by(DatasetVersion.version.desc())
    ).all()
    return {
        "id": dataset.id,
        "name": dataset.name,
        "status": dataset.status,
        "created_at": dataset.created_at.isoformat(),
        "versions": [_version_payload(item) for item in versions],
    }


@router.post("/{dataset_id}/versions", status_code=201)
async def add_version(
    dataset_id: str,
    file: Annotated[UploadFile, File(description="CSV or XLSX history")],
    user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
) -> dict:
    dataset = _dataset_for_user(db, user.id, dataset_id)
    filename = _safe_filename(file.filename)
    subscription = get_subscription(db, user.id)
    assert subscription is not None
    plan = get_plan(subscription.plan_code)
    content = await file.read(plan.max_upload_mb * 1024 * 1024 + 1)
    version = _create_version(
        db=db,
        user=user,
        dataset=dataset,
        filename=filename,
        content_type=file.content_type or "application/octet-stream",
        content=content,
    )
    return _version_payload(version)


@router.get("/{dataset_id}/versions/{version_id}/source")
def download_source(
    dataset_id: str,
    version_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    _dataset_for_user(db, user.id, dataset_id)
    version = db.scalar(
        select(DatasetVersion).where(
            DatasetVersion.id == version_id,
            DatasetVersion.dataset_id == dataset_id,
            DatasetVersion.user_id == user.id,
        )
    )
    if version is None:
        raise HTTPException(status_code=404, detail="Версия не найдена.")
    content = storage.get_bytes(version.source_key)
    return StreamingResponse(
        io.BytesIO(content),
        media_type=version.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{version.original_filename}"'
        },
    )

import logging
from pathlib import Path
logger = logging.getLogger(__name__)


@router.delete("/{dataset_id}", status_code=204)
def delete_dataset(
    dataset_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    dataset = _dataset_for_user(db, user.id, dataset_id)

    storage_prefix = f"users/{user.id}/datasets/{dataset_id}"

    db.delete(dataset)
    db.commit()

    try:
        storage.delete_prefix(storage_prefix)
    except Exception:
        logger.exception(
            "Не удалось удалить файлы датасета: %s",
            storage_prefix,
        )


