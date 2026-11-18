from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str
    api_key: str
    data_dir: Path
    cors_origins: tuple[str, ...]
    max_upload_mb: int
    log_level: str
    execution_mode: str
    auto_publish_enabled: bool
    approval_wape_max: float
    auto_wape_max: float
    max_auto_price_change: float
    database_url: str
    jwt_secret: str
    access_token_minutes: int
    refresh_token_days: int
    cookie_secure: bool
    storage_backend: str
    object_store_dir: Path
    s3_endpoint_url: str
    s3_region: str
    s3_bucket: str
    s3_access_key_id: str
    s3_secret_access_key: str
    s3_sse: str
    app_url: str
    yookassa_shop_id: str
    yookassa_secret_key: str
    yookassa_return_url: str
    admin_api_key: str
    trial_days: int
    basic_plan_rub: int
    business_plan_rub: int
    worker_poll_seconds: float

    @classmethod
    def from_env(cls) -> "Settings":
        raw_origins = os.getenv("SUPPLYMARGIN_CORS_ORIGINS", "http://localhost:8000")
        data_dir = Path(os.getenv("SUPPLYMARGIN_DATA_DIR", str(PROJECT_ROOT / "data")))
        execution_mode = os.getenv("SUPPLYMARGIN_MODE", "shadow").strip().lower()
        if execution_mode not in {"shadow", "approval", "auto"}:
            raise ValueError(
                "SUPPLYMARGIN_MODE должен быть shadow, approval или auto."
            )
        storage_backend = os.getenv("SUPPLYMARGIN_STORAGE_BACKEND", "local").lower()
        if storage_backend not in {"local", "s3"}:
            raise ValueError("SUPPLYMARGIN_STORAGE_BACKEND должен быть local или s3.")
        app_url = os.getenv("SUPPLYMARGIN_APP_URL", "http://localhost:8000").rstrip("/")
        return cls(
            environment=os.getenv("SUPPLYMARGIN_ENV", "development"),
            api_key=os.getenv("SUPPLYMARGIN_API_KEY", ""),
            data_dir=data_dir,
            cors_origins=tuple(x.strip() for x in raw_origins.split(",") if x.strip()),
            max_upload_mb=max(1, int(os.getenv("SUPPLYMARGIN_MAX_UPLOAD_MB", "128"))),
            log_level=os.getenv("SUPPLYMARGIN_LOG_LEVEL", "INFO").upper(),
            execution_mode=execution_mode,
            auto_publish_enabled=(
                os.getenv("SUPPLYMARGIN_AUTO_PUBLISH_ENABLED", "false").lower()
                in {"1", "true", "yes"}
            ),
            approval_wape_max=float(
                os.getenv("SUPPLYMARGIN_APPROVAL_WAPE_MAX", "0.50")
            ),
            auto_wape_max=float(os.getenv("SUPPLYMARGIN_AUTO_WAPE_MAX", "0.35")),
            max_auto_price_change=float(
                os.getenv("SUPPLYMARGIN_MAX_AUTO_PRICE_CHANGE", "0.05")
            ),
            database_url=os.getenv(
                "SUPPLYMARGIN_DATABASE_URL",
                f"sqlite:///{data_dir / 'results' / 'supplymargin_saas.sqlite3'}",
            ),
            jwt_secret=os.getenv(
                "SUPPLYMARGIN_JWT_SECRET",
                "change-me-in-production-use-at-least-64-random-characters",
            ),
            access_token_minutes=max(
                5, int(os.getenv("SUPPLYMARGIN_ACCESS_TOKEN_MINUTES", "15"))
            ),
            refresh_token_days=max(
                1, int(os.getenv("SUPPLYMARGIN_REFRESH_TOKEN_DAYS", "30"))
            ),
            cookie_secure=os.getenv("SUPPLYMARGIN_COOKIE_SECURE", "false").lower()
            in {"1", "true", "yes"},
            storage_backend=storage_backend,
            object_store_dir=Path(
                os.getenv(
                    "SUPPLYMARGIN_OBJECT_STORE_DIR",
                    str(data_dir / "objects"),
                )
            ),
            s3_endpoint_url=os.getenv("SUPPLYMARGIN_S3_ENDPOINT_URL", ""),
            s3_region=os.getenv("SUPPLYMARGIN_S3_REGION", "ru-1"),
            s3_bucket=os.getenv("SUPPLYMARGIN_S3_BUCKET", "supplymargin-production"),
            s3_access_key_id=os.getenv("SUPPLYMARGIN_S3_ACCESS_KEY_ID", ""),
            s3_secret_access_key=os.getenv("SUPPLYMARGIN_S3_SECRET_ACCESS_KEY", ""),
            s3_sse=os.getenv("SUPPLYMARGIN_S3_SSE", ""),
            app_url=app_url,
            yookassa_shop_id=os.getenv("SUPPLYMARGIN_YOOKASSA_SHOP_ID", ""),
            yookassa_secret_key=os.getenv("SUPPLYMARGIN_YOOKASSA_SECRET_KEY", ""),
            yookassa_return_url=os.getenv(
                "SUPPLYMARGIN_YOOKASSA_RETURN_URL", f"{app_url}/?payment=return"
            ),
            admin_api_key=os.getenv("SUPPLYMARGIN_ADMIN_API_KEY", ""),
            trial_days=max(0, int(os.getenv("SUPPLYMARGIN_TRIAL_DAYS", "0"))),
            basic_plan_rub=max(1, int(os.getenv("SUPPLYMARGIN_BASIC_PLAN_RUB", "1990"))),
            business_plan_rub=max(
                1, int(os.getenv("SUPPLYMARGIN_BUSINESS_PLAN_RUB", "2990"))
            ),
            worker_poll_seconds=max(
                0.2, float(os.getenv("SUPPLYMARGIN_WORKER_POLL_SECONDS", "2"))
            ),
        )

    @property
    def demo_file(self) -> Path:
        return self.data_dir / "demo" / "retail_price_history.csv"

    @property
    def market_file(self) -> Path:
        return self.data_dir / "demo" / "market_snapshot_2026-07-25.json"

    @property
    def results_dir(self) -> Path:
        return self.data_dir / "results"


settings = Settings.from_env()
