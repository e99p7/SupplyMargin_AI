from __future__ import annotations

from dataclasses import asdict, dataclass

from supplymargin.config import settings


@dataclass(frozen=True, slots=True)
class Plan:
    code: str
    name_ru: str
    price_rub: int
    monthly_runs: int
    max_datasets: int
    max_upload_mb: int

    def public(self) -> dict:
        return asdict(self)


def all_plans() -> dict[str, Plan]:
    return {
        "basic": Plan(
            code="basic",
            name_ru="Базовый",
            price_rub=settings.basic_plan_rub,
            monthly_runs=5,
            max_datasets=10,
            max_upload_mb=min(20, settings.max_upload_mb),
        ),
        "business": Plan(
            code="business",
            name_ru="Бизнес",
            price_rub=settings.business_plan_rub,
            monthly_runs=300,
            max_datasets=100,
            max_upload_mb=min(100, settings.max_upload_mb),
        ),
    }


def get_plan(code: str) -> Plan:
    plan = all_plans().get(code)
    if plan is None:
        raise ValueError(f"Неизвестный тариф: {code}")
    return plan
