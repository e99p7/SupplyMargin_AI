from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import pandas as pd
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from supplymargin import __version__
from supplymargin.config import settings
from supplymargin.data import load_history
from supplymargin.database import database_ping, init_database
from supplymargin.integration import integrate_operational_files
from supplymargin.market import load_market_snapshot
from supplymargin.object_storage import storage as object_storage
from supplymargin.quality import analyze_history_quality
from supplymargin.routers import admin as admin_router
from supplymargin.routers import auth as auth_router
from supplymargin.routers import billing as billing_router
from supplymargin.routers import datasets as datasets_router
from supplymargin.routers import runs as runs_router
from supplymargin.schemas import (
    DecisionRequest,
    OptimizeOptions,
    OptimizeRequest,
    OptimizeResponse,
    OutcomeRequest,
)
from supplymargin.service import (
    build_shadow_report,
    register_outcomes,
    run_from_frame,
    run_optimization,
)
from supplymargin.storage import RunStore

from sqladmin import Admin, ModelView

from .auth import password_hasher
from .database import engine
from .models import User, Subscription

from sqladmin import ModelView
from wtforms import PasswordField
from wtforms.validators import DataRequired, Optional
from pwdlib import PasswordHash

logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("supplymargin.api")
REQUESTS = Counter(
    "supplymargin_http_requests_total",
    "HTTP requests",
    ["method", "path", "status"],
)
LATENCY = Histogram(
    "supplymargin_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "path"],
)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_database()
    if settings.environment == "production" and (
        len(settings.jwt_secret) < 32
        or settings.jwt_secret.startswith(("change-me", "CHANGE_ME"))
    ):
        raise RuntimeError(
            "SUPPLYMARGIN_JWT_SECRET must contain at least 32 random characters"
        )
    yield


app = FastAPI(
    title="SupplyMargin AI API",
    version=__version__,
    description=(
        "Demand forecasting, landed-cost, procurement ceiling, retail price "
        "and order optimization."
    ),
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key", "X-Admin-Key"],
)
store = RunStore(settings.results_dir)
STATIC_DIR = Path(__file__).with_name("static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.include_router(auth_router.router)
app.include_router(billing_router.router)
app.include_router(datasets_router.router)
app.include_router(runs_router.router)
app.include_router(admin_router.router)


@app.middleware("http")
async def metrics_middleware(request, call_next):
    started = time.perf_counter()
    try:
        response = await call_next(request)
        status = str(response.status_code)
    except Exception:
        status = "500"
        raise
    finally:
        path = request.url.path
        REQUESTS.labels(request.method, path, status).inc()
        LATENCY.labels(request.method, path).observe(time.perf_counter() - started)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


def require_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    if settings.environment == "production" and not settings.api_key:
        raise HTTPException(status_code=404, detail="Not found.")
    if settings.api_key and x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Неверный или отсутствующий API key.")


def current_market() -> dict:
    try:
        return load_market_snapshot(settings.market_file)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=f"Market snapshot unavailable: {exc}")


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/widget.js", include_in_schema=False)
def widget() -> FileResponse:
    return FileResponse(STATIC_DIR / "widget.js", media_type="application/javascript")


@app.get("/healthz")
def health() -> dict:
    return {
        "status": "ok",
        "service": "supplymargin-ai",
        "version": __version__,
        "mode": settings.execution_mode,
    }


@app.get("/readyz")
def ready() -> JSONResponse:
    database_ready = database_ping()
    object_storage_ready = object_storage.ping()
    ready_value = (
        store.ping()
        and database_ready
        and object_storage_ready
        and settings.demo_file.exists()
        and settings.market_file.exists()
    )
    return JSONResponse(
        status_code=200 if ready_value else 503,
        content={
            "status": "ready" if ready_value else "not_ready",
            "storage": store.ping(),
            "database": database_ready,
            "object_storage": object_storage_ready,
            "demo_data": settings.demo_file.exists(),
            "market_snapshot": settings.market_file.exists(),
            "execution_mode": settings.execution_mode,
            "auto_publish_enabled": settings.auto_publish_enabled,
        },
    )


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/v1/market")
def market() -> dict:
    return current_market()


@app.get("/v1/system/mode")
def system_mode() -> dict:
    return {
        "mode": settings.execution_mode,
        "auto_publish_enabled": settings.auto_publish_enabled,
        "publication_adapter_configured": False,
        "safe_default": "shadow",
        "message_ru": (
            "Сервис формирует и измеряет рекомендации, но не изменяет цены "
            "во внешней master-системе."
        ),
    }


@app.post(
    "/v1/optimize",
    response_model=OptimizeResponse,
    dependencies=[Depends(require_api_key)],
)
def optimize_json(request: OptimizeRequest) -> dict:
    try:
        frame = pd.DataFrame([item.model_dump() for item in request.records])
        return run_from_frame(
            frame=frame,
            market=current_market(),
            options=request.options,
            store=store,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post(
    "/v1/optimize/upload",
    response_model=OptimizeResponse,
    dependencies=[Depends(require_api_key)],
)
async def optimize_upload(
    file: Annotated[UploadFile, File(description="CSV history")],
    horizon: Annotated[int, Form(ge=1, le=365)] = 14,
    min_margin: Annotated[float, Form(ge=0, le=0.9)] = 0.15,
    max_price_change: Annotated[float, Form(ge=0, le=1)] = 0.15,
    service_level: Annotated[float, Form(ge=0.5, le=0.999)] = 0.90,
) -> dict:
    content = await file.read()
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail="CSV превышает допустимый размер.")
    try:
        normalized = load_history(content)
        options = OptimizeOptions(
            horizon=horizon,
            min_margin=min_margin,
            max_price_change=max_price_change,
            service_level=service_level,
        )
        return run_optimization(
            normalized=normalized,
            market=current_market(),
            options=options,
            store=store,
        )
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post(
    "/v1/optimize/operational-upload",
    response_model=OptimizeResponse,
    dependencies=[Depends(require_api_key)],
)
async def optimize_operational_upload(
    sales: Annotated[UploadFile, File(description="ERP sales CSV")],
    purchase_costs: Annotated[
        UploadFile, File(description="ERP purchase-cost history CSV")
    ],
    daily_stock: Annotated[UploadFile, File(description="WMS daily stock CSV")],
    logistics_costs: Annotated[
        UploadFile, File(description="TMS/accounting logistics costs CSV")
    ],
    horizon: Annotated[int, Form(ge=1, le=365)] = 14,
    min_margin: Annotated[float, Form(ge=0, le=0.9)] = 0.15,
    max_price_change: Annotated[float, Form(ge=0, le=1)] = 0.15,
    service_level: Annotated[float, Form(ge=0.5, le=0.999)] = 0.90,
    inventory_tolerance_days: Annotated[int, Form(ge=0, le=31)] = 7,
    logistics_tolerance_days: Annotated[int, Form(ge=0, le=730)] = 180,
) -> dict:
    contents = [
        await sales.read(),
        await purchase_costs.read(),
        await daily_stock.read(),
        await logistics_costs.read(),
    ]
    if any(len(content) > settings.max_upload_mb * 1024 * 1024 for content in contents):
        raise HTTPException(
            status_code=413,
            detail="Один из CSV превышает допустимый размер.",
        )
    try:
        integrated = integrate_operational_files(
            sales=contents[0],
            purchase_costs=contents[1],
            daily_stock=contents[2],
            logistics_costs=contents[3],
            inventory_tolerance_days=inventory_tolerance_days,
            logistics_tolerance_days=logistics_tolerance_days,
        )
        payload = run_optimization(
            normalized=integrated.normalized,
            market=current_market(),
            options=OptimizeOptions(
                horizon=horizon,
                min_margin=min_margin,
                max_price_change=max_price_change,
                service_level=service_level,
            ),
            store=store,
        )
        payload["integration_report"] = integrated.report
        return payload
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post(
    "/v1/history/quality/upload",
    dependencies=[Depends(require_api_key)],
)
async def history_quality_upload(
    file: Annotated[UploadFile, File(description="CSV history")],
) -> dict:
    content = await file.read()
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail="CSV превышает допустимый размер.")
    try:
        normalized = load_history(content)
        return {
            "quality": analyze_history_quality(normalized.frame),
            "normalization_warnings": normalized.warnings,
        }
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post(
    "/v1/demo/run",
    response_model=OptimizeResponse,
    dependencies=[Depends(require_api_key)],
)
def demo_run() -> dict:
    try:
        return run_optimization(
            normalized=load_history(settings.demo_file),
            market=current_market(),
            options=OptimizeOptions(horizon=3),
            store=store,
            output_dir=settings.results_dir,
        )
    except (ValueError, FileNotFoundError) as exc:
        logger.exception("Demo run failed")
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/legacy/runs", dependencies=[Depends(require_api_key)])
def list_runs(limit: int = 20) -> list[dict]:
    return store.list(limit)


@app.get("/v1/legacy/runs/{run_id}", dependencies=[Depends(require_api_key)])
def get_run(run_id: str) -> dict:
    result = store.get(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    return result


@app.post("/v1/decisions", dependencies=[Depends(require_api_key)])
def record_decision(request: DecisionRequest) -> dict:
    if settings.execution_mode == "shadow":
        raise HTTPException(
            status_code=409,
            detail="В shadow-mode решения отключены: сначала накопите фактические результаты.",
        )
    recommendation = store.get_recommendation(request.run_id, request.sku)
    if recommendation is None:
        raise HTTPException(status_code=404, detail="Recommendation not found.")
    if (
        request.decision in {"approve", "override"}
        and not recommendation.get("eligible_for_approval", False)
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Рекомендация заблокирована quality-gate.",
                "reasons": recommendation.get("approval_gate_reasons", []),
            },
        )
    if request.override_price is not None:
        price_floor = float(recommendation.get("safe_price_min", 0))
        price_ceiling = float(
            recommendation.get("safe_price_max", float("inf"))
        )
        if request.override_price < price_floor or request.override_price > price_ceiling:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"override_price должен быть в безопасном диапазоне "
                    f"[{price_floor:.2f}, {price_ceiling:.2f}]."
                ),
            )
    updated = store.record_decision(
        run_id=request.run_id,
        sku=request.sku,
        decision=request.decision,
        reason=request.reason,
        override_price=request.override_price,
        decided_at=datetime.now(timezone.utc).isoformat(),
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Recommendation not found.")
    return updated


@app.post("/v1/outcomes", dependencies=[Depends(require_api_key)])
def save_outcomes(request: OutcomeRequest) -> dict:
    try:
        return register_outcomes(
            store=store,
            outcomes=[row.model_dump() for row in request.outcomes],
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get(
    "/v1/shadow/report/{run_id}",
    dependencies=[Depends(require_api_key)],
)
def shadow_report(run_id: str) -> dict:
    if store.get(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    return build_shadow_report(store=store, run_id=run_id)

# Подключаем административную панель
admin = Admin(app, engine)

password_hash = PasswordHash.recommended()

class UserAdmin(ModelView, model=User):
    name = "Пользователь"
    name_plural = "Пользователи"

    column_list = [
        User.id,
        User.email,
        User.is_active,
        User.is_admin,
        User.created_at,
    ]

    column_searchable_list = [
        User.email,
    ]

    column_sortable_list = [
        User.email,
        User.created_at,
    ]

    # Поля формы
    form_columns = [
        User.email,
        User.password_hash,
        User.is_active,
        User.is_admin,
    ]

    # password_hash отображаем как PasswordField
    form_overrides = {
        "password_hash": PasswordField,
    }

    # Настройка подписи поля
    form_args = {
        "password_hash": {
            "label": "Пароль",
            "validators": [Optional()],
        },
    }

    async def insert_model(self, request, data):
        # Здесь password_hash пока содержит ОБЫЧНЫЙ пароль
        password = data.get("password_hash")

        if not password:
            raise ValueError("Пароль обязателен")

        # Хешируем пароль
        data["password_hash"] = password_hash.hash(password)

        # Генерируем UUID
        data["id"] = str(uuid.uuid4())

        return await super().insert_model(request, data)

    async def update_model(self, request, pk, data):
        # Если пароль не ввели — оставляем старый
        password = data.get("password_hash")

        if password:
            data["password_hash"] = password_hash.hash(password)
        else:
            # Не меняем существующий password_hash
            data.pop("password_hash", None)

        return await super().update_model(request, pk, data)

class SubscriptionAdmin(ModelView, model=Subscription):
    name = "Подписка"
    name_plural = "Подписки"

    column_list = [
        Subscription.id,
        Subscription.user,
        Subscription.plan_code,
        Subscription.status,
        Subscription.provider,
        Subscription.current_period_start,
        Subscription.current_period_end,
        Subscription.cancel_at_period_end,
    ]

    column_searchable_list = [
        Subscription.plan_code,
        Subscription.status,
    ]

    column_sortable_list = [
        Subscription.plan_code,
        Subscription.status,
        Subscription.current_period_start,
        Subscription.current_period_end,
    ]

    form_columns = [
        Subscription.user,
        Subscription.plan_code,
        Subscription.status,
        Subscription.provider,
        Subscription.provider_payment_method_id,
        Subscription.current_period_start,
        Subscription.current_period_end,
        Subscription.cancel_at_period_end,
    ]

    async def insert_model(self, request, data):
        data["id"] = str(uuid.uuid4())

        return await super().insert_model(request, data)


# Регистрируем модель в панели
admin.add_view(UserAdmin)
admin.add_view(SubscriptionAdmin)
