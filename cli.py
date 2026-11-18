from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from supplymargin.config import settings
from supplymargin.data import load_history
from supplymargin.integration import integrate_operational_files
from supplymargin.market import CbrClient, load_market_snapshot
from supplymargin.quality import analyze_history_quality
from supplymargin.schemas import OptimizeOptions
from supplymargin.service import build_shadow_report, register_outcomes, run_optimization
from supplymargin.storage import RunStore


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="supplymargin", description="SupplyMargin AI CLI")
    commands = root.add_subparsers(dest="command", required=True)

    demo = commands.add_parser("demo", help="Run bundled real-data demo")
    demo.add_argument("--horizon", type=int, default=3)
    demo.add_argument("--output", default=str(settings.results_dir))

    optimize = commands.add_parser("optimize", help="Optimize a canonical/legacy CSV")
    optimize.add_argument("--input", required=True)
    optimize.add_argument("--horizon", type=int, default=14)
    optimize.add_argument("--min-margin", type=float, default=0.15)
    optimize.add_argument("--max-price-change", type=float, default=0.15)
    optimize.add_argument("--service-level", type=float, default=0.90)
    optimize.add_argument("--risk-aversion", type=float, default=0.25)
    optimize.add_argument("--output", default=str(settings.results_dir))

    integrate = commands.add_parser(
        "integrate-data",
        help="Join separate ERP sales/costs, WMS stock and TMS logistics exports",
    )
    operational = commands.add_parser(
        "optimize-operational",
        help="Join four operational exports and immediately run optimization",
    )
    for command in (integrate, operational):
        command.add_argument("--sales", required=True)
        command.add_argument("--purchase-costs", required=True)
        command.add_argument("--daily-stock", required=True)
        command.add_argument("--logistics-costs", required=True)
        command.add_argument("--inventory-tolerance-days", type=int, default=7)
        command.add_argument("--logistics-tolerance-days", type=int, default=180)
        command.add_argument("--output", required=True)
    operational.add_argument("--horizon", type=int, default=14)
    operational.add_argument("--min-margin", type=float, default=0.15)
    operational.add_argument("--max-price-change", type=float, default=0.15)
    operational.add_argument("--service-level", type=float, default=0.90)
    operational.add_argument("--risk-aversion", type=float, default=0.25)

    history_report = commands.add_parser(
        "history-report",
        help="Audit history coverage and price variation before optimization",
    )
    history_report.add_argument("--input", required=True)
    history_report.add_argument("--output")

    outcomes = commands.add_parser(
        "record-outcomes",
        help="Load actual sales CSV and update shadow-mode evaluation",
    )
    outcomes.add_argument("--input", required=True)

    shadow_report = commands.add_parser(
        "shadow-report",
        help="Show forecast-vs-actual report for a run",
    )
    shadow_report.add_argument("--run-id", required=True)

    commands.add_parser("refresh-market", help="Refresh CBR official macro data")
    return root


def main() -> None:
    args = parser().parse_args()
    if args.command == "refresh-market":
        snapshot = CbrClient().refresh_snapshot(settings.market_file)
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
        return
    if args.command == "history-report":
        report = analyze_history_quality(load_history(Path(args.input)).frame)
        rendered = json.dumps(report, ensure_ascii=False, indent=2)
        if args.output:
            Path(args.output).write_text(rendered, encoding="utf-8")
            print(f"Отчет: {Path(args.output).resolve()}")
        else:
            print(rendered)
        return
    if args.command == "record-outcomes":
        frame = pd.read_csv(args.input)
        required = {
            "run_id",
            "sku",
            "actual_date",
            "actual_sell_price",
            "actual_units",
            "actual_purchase_cost",
        }
        missing = sorted(required - set(frame.columns))
        if missing:
            raise SystemExit(
                "В outcomes CSV отсутствуют колонки: " + ", ".join(missing)
            )
        if "actual_shipping_cost" not in frame:
            frame["actual_shipping_cost"] = 0.0
        rows = frame.where(pd.notna(frame), None).to_dict(orient="records")
        result = register_outcomes(
            store=RunStore(settings.results_dir),
            outcomes=rows,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "shadow-report":
        report = build_shadow_report(
            store=RunStore(settings.results_dir),
            run_id=args.run_id,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    if args.command in {"integrate-data", "optimize-operational"}:
        integrated = integrate_operational_files(
            sales=Path(args.sales),
            purchase_costs=Path(args.purchase_costs),
            daily_stock=Path(args.daily_stock),
            logistics_costs=Path(args.logistics_costs),
            inventory_tolerance_days=args.inventory_tolerance_days,
            logistics_tolerance_days=args.logistics_tolerance_days,
        )
        output = Path(args.output)
        output.mkdir(parents=True, exist_ok=True)
        canonical_file = output / "integrated_history.csv"
        report_file = output / "integration_report.json"
        integrated.normalized.frame.to_csv(canonical_file, index=False)
        report_file.write_text(
            json.dumps(integrated.report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Объединенная история: {canonical_file.resolve()}")
        print(f"Отчет интеграции: {report_file.resolve()}")
        if args.command == "integrate-data":
            return
        payload = run_optimization(
            normalized=integrated.normalized,
            market=load_market_snapshot(settings.market_file),
            options=OptimizeOptions(
                horizon=args.horizon,
                min_margin=args.min_margin,
                max_price_change=args.max_price_change,
                service_level=args.service_level,
                risk_aversion=args.risk_aversion,
            ),
            store=RunStore(settings.results_dir),
            output_dir=output,
        )
        print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
        print(f"Результат: {(output / 'latest_recommendations.csv').resolve()}")
        return

    source = settings.demo_file if args.command == "demo" else Path(args.input)
    options = OptimizeOptions(
        horizon=args.horizon,
        min_margin=getattr(args, "min_margin", 0.15),
        max_price_change=getattr(args, "max_price_change", 0.15),
        service_level=getattr(args, "service_level", 0.90),
        risk_aversion=getattr(args, "risk_aversion", 0.25),
    )
    payload = run_optimization(
        normalized=load_history(source),
        market=load_market_snapshot(settings.market_file),
        options=options,
        store=RunStore(settings.results_dir),
        output_dir=args.output,
        force_forecast_only=(args.command == "demo"),
    )
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print(f"Результат: {Path(args.output).resolve() / 'latest_recommendations.csv'}")


if __name__ == "__main__":
    main()
