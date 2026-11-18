from __future__ import annotations

import json
from pathlib import Path


def markdown(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": text.splitlines(True)}


def code(text: str) -> dict:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": text.splitlines(True),
    }


cells = [
    markdown(
        """# SupplyMargin AI — Google Colab

Эта тетрадка запускает **ту же production-модель**, что API и Docker, а не
упрощенный пример. Загрузите полученный архив `SupplyMargin_AI.zip`.

Результат: прогноз спроса, price corridor, целевая и максимально допустимая
закупочная цена, цена продажи и объем заказа по каждому SKU. Версия 1.2 также
объединяет отдельные ERP/WMS/TMS-выгрузки и проверяет происхождение каждого
компонента себестоимости.
"""
    ),
    code(
        """from google.colab import files
uploaded = files.upload()
archive = next(name for name in uploaded if name.lower().endswith(".zip"))
!rm -rf /content/SupplyMargin_AI
!unzip -q "$archive" -d /content
%cd /content/SupplyMargin_AI
"""
    ),
    code(
        """%pip install -q -r requirements.txt
print("Dependencies installed")
"""
    ),
    markdown(
        """## Запуск комплектного реального demo

Реальны исходные продажи, цены и цены конкурентов. Закупочная цена/остатки,
которых нет в открытом датасете, помечены как производные demo-поля.
"""
    ),
    code(
        """!python cli.py demo --horizon 3 --output /content/supplymargin_result
"""
    ),
    code(
        """import pandas as pd
from IPython.display import display
result = pd.read_csv("/content/supplymargin_result/latest_recommendations.csv")
display(result.head(20))
"""
    ),
    code(
        """import matplotlib.pyplot as plt
top = result.nlargest(15, "expected_profit_after").sort_values("expected_profit_after")
ax = top.plot(
    x="sku",
    y=["expected_profit_before", "expected_profit_after"],
    kind="barh",
    figsize=(12, 7),
    color=["#b8c1bb", "#0c7b5a"],
)
ax.set_title("Ожидаемая прибыль до и после рекомендации")
ax.set_xlabel("Денежные единицы исходного датасета")
plt.tight_layout()
plt.show()
"""
    ),
    markdown(
        """## Production-вариант: четыре выгрузки

Подготовьте `sales.csv`, `purchase_costs.csv`, `daily_stock.csv` и
`logistics_costs.csv` по контракту `docs/data_contract_ru.md`. Модель выполнит
as-of join и не назовет пропущенные затраты фактическими.
"""
    ),
    code(
        """operational = files.upload()
required = {
    "sales.csv", "purchase_costs.csv", "daily_stock.csv", "logistics_costs.csv"
}
missing = required - set(operational)
assert not missing, f"Не загружены файлы: {sorted(missing)}"
!python cli.py optimize-operational \
  --sales sales.csv \
  --purchase-costs purchase_costs.csv \
  --daily-stock daily_stock.csv \
  --logistics-costs logistics_costs.csv \
  --horizon 14 \
  --output /content/operational_result
"""
    ),
    code(
        """import json
integration = json.load(
    open("/content/operational_result/integration_report.json", encoding="utf-8")
)
print("Actual coverage:", integration["actual_coverage"])
operational_result = pd.read_csv(
    "/content/operational_result/latest_recommendations.csv"
)
display(operational_result.head(50))
"""
    ),
    markdown(
        """## Альтернатива: готовая canonical CSV

Формат описан в `docs/data_contract_ru.md`. Загрузите файл и выполните ячейку.
"""
    ),
    code(
        """own = files.upload()
csv_name = next(name for name in own if name.lower().endswith(".csv"))
!python cli.py history-report --input "$csv_name" --output /content/history_quality.json
import json
quality = json.load(open("/content/history_quality.json", encoding="utf-8"))
print("History quality:", quality["grade"])
display(pd.DataFrame(quality["per_sku"]).head(50))
"""
    ),
    code(
        """# Запускайте расчет после просмотра проблемных SKU
!python cli.py optimize --input "$csv_name" --horizon 14 --output /content/own_result
own_result = pd.read_csv("/content/own_result/latest_recommendations.csv")
display(own_result.head(50))
"""
    ),
    markdown(
        """## Скачать результат

Рекомендации уже имеют статус `shadow_observation` и не публикуются. После
наблюдаемого периода загрузите фактические продажи через `record-outcomes`;
причинный эффект проверяйте A/B или switchback-тестом.
"""
    ),
    code(
        """files.download("/content/own_result/latest_recommendations.csv")
"""
    ),
]

notebook = {
    "cells": cells,
    "metadata": {
        "accelerator": "CPU",
        "colab": {"name": "SupplyMargin_AI_Colab.ipynb", "provenance": []},
        "kernelspec": {"display_name": "Python 3", "name": "python3"},
        "language_info": {"name": "python", "version": "3"},
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}

target = Path(__file__).resolve().parents[1] / "notebooks" / "SupplyMargin_AI_Colab.ipynb"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(notebook, ensure_ascii=False, indent=1), encoding="utf-8")
print(target)
