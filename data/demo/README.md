# Demo data provenance

## `retail_price_history.csv`

Файл перенесен без изменения исходных полей из прежнего проекта пользователя:

`https://github.com/e99p7/GA-optimizing-prices-to-maximize-profits`

Это публичный e-commerce price-optimization dataset: 676 помесячных строк,
товарные категории, объем продаж, retail price, freight price и цены трех
конкурентов. Его также можно обнаружить по тем же полям (`comp_1`, `ps1`,
`fp1`, `lag_price`) в Kaggle Retail Price Optimization.

SupplyMargin AI сохраняет реальные наблюдаемые поля. Закрытых procurement cost,
stock, handling и storage в источнике нет, поэтому legacy demo adapter создает
их только для технической демонстрации. Строки получают:

- `purchase_cost_source=estimated`;
- `stock_source=estimated`;
- `shipping_cost_source=actual`;
- `handling_cost_source=estimated`;
- `storage_cost_source=estimated`.

Поэтому demo рассчитывается в shadow-mode, но не проходит future
approval/auto data gate.

## `market_snapshot_2026-07-25.json`

Фиксированный воспроизводимый снимок официальной макроинформации на 25.07.2026.
URL источников находятся внутри JSON. Команда `python cli.py refresh-market`
обновляет значения из официальной таблицы Банка России.

## Дополнительный UCI источник

`scripts/download_uci.py` загружает Online Retail:

Chen, D. (2015). Online Retail [Dataset]. UCI Machine Learning Repository.
https://doi.org/10.24432/C5BW33

License: CC BY 4.0.

## Супермаркет: закупочные цены и ежедневные остатки

`scripts/download_supermarket_wiley.py` загружает пять Supporting Information
XLSX к DOI `10.1002/nav.21957`: 28 757 SKU, ежедневные продажи, остатки,
закупочные цены и пополнения. Фактических логистических счетов источник не
содержит; подробности — `docs/open_data_ru.md`.
