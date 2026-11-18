from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)


class HistoryRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    sku: str
    date: str
    units: float = Field(ge=0)
    sell_price: float = Field(gt=0)
    purchase_cost: float = Field(gt=0)
    shipping_cost: float = Field(default=0, ge=0)
    handling_cost: float = Field(default=0, ge=0)
    storage_cost_per_unit_day: float = Field(default=0, ge=0)
    stock: float = Field(default=0, ge=0)
    on_order: float = Field(default=0, ge=0)
    lead_time_days: int = Field(default=14, ge=0, le=365)
    category: str = "unknown"
    competitor_price: float | None = Field(default=None, gt=0)
    min_price: float | None = Field(default=None, gt=0)
    max_price: float | None = Field(default=None, gt=0)
    purchase_cost_source: Literal["actual", "estimated", "missing"] = "actual"
    stock_source: Literal["actual", "estimated", "missing"] = "actual"
    shipping_cost_source: Literal["actual", "estimated", "missing"] = "actual"
    handling_cost_source: Literal["actual", "estimated", "missing"] = "missing"
    storage_cost_source: Literal["actual", "estimated", "missing"] = "missing"


class OptimizeOptions(BaseModel):
    horizon: int = Field(default=14, ge=1, le=365)
    min_margin: float = Field(default=0.15, ge=0, le=0.9)
    max_price_change: float = Field(default=0.15, ge=0, le=1)
    service_level: float = Field(default=0.90, ge=0.5, le=0.999)
    risk_aversion: float = Field(default=0.25, ge=0, le=2)
    random_seed: int = 42

    @field_validator("horizon")
    @classmethod
    def horizon_is_integer(cls, value: int) -> int:
        return int(value)


class OptimizeRequest(BaseModel):
    records: list[HistoryRecord] = Field(min_length=8)
    options: OptimizeOptions = Field(default_factory=OptimizeOptions)


class RunSummary(BaseModel):
    run_id: str
    created_at: str
    as_of: str
    rows_used: int
    sku_count: int
    model_name: str
    validation_wape: float
    horizon_periods: int
    expected_profit_before: float
    expected_profit_after: float
    expected_profit_gain: float
    low_confidence_skus: int
    execution_mode: Literal["shadow", "approval", "auto"]
    history_quality_grade: Literal["A", "B", "C", "D"]
    production_data_ready_skus: int


class OptimizeResponse(BaseModel):
    summary: RunSummary
    recommendations: list[dict[str, Any]]
    market: dict[str, Any]
    warnings: list[str]
    execution: dict[str, Any]
    history_quality: dict[str, Any]
    data_lineage: dict[str, Any]
    integration_report: dict[str, Any] | None = None


class DecisionRequest(BaseModel):
    run_id: str
    sku: str
    decision: Literal["approve", "reject", "override"]
    reason: str = Field(min_length=3, max_length=500)
    override_price: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def override_required_for_override(self):
        if self.decision == "override" and self.override_price is None:
            raise ValueError("Для override требуется override_price.")
        return self


class OutcomeRecord(BaseModel):
    run_id: str
    sku: str
    actual_date: str
    actual_sell_price: float = Field(gt=0)
    actual_units: float = Field(ge=0)
    actual_purchase_cost: float = Field(gt=0)
    actual_shipping_cost: float = Field(default=0, ge=0)
    stock_end: float | None = Field(default=None, ge=0)


class OutcomeRequest(BaseModel):
    outcomes: list[OutcomeRecord] = Field(min_length=1, max_length=10000)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=7, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class UserResponse(BaseModel):
    id: str
    email: EmailStr
    is_admin: bool
    created_at: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


class CheckoutRequest(BaseModel):
    plan_code: Literal["basic", "business"] = "basic"


class RunCreateRequest(BaseModel):
    dataset_version_id: str
    options: OptimizeOptions = Field(default_factory=OptimizeOptions)


class AdminSubscriptionRequest(BaseModel):
    plan_code: Literal["basic", "business"] = "basic"
    days: int = Field(default=30, ge=1, le=366)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=7, max_length=200)


class DeleteAccountRequest(BaseModel):
    password: str = Field(min_length=1, max_length=200)
