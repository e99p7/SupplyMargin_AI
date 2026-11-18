# Контракт данных

Production-вариант принимает четыре независимые таблицы. Это соответствует
тому, как данные реально хранятся в ERP/WMS/TMS. Сервис сам выполняет
temporal/as-of join по `sku` и `date`, не требует искусственно собирать одну
таблицу вручную и не смешивает SKU разных организаций.

## Рекомендуемый production-контракт: четыре CSV

### `sales.csv` — ERP/POS

Обязательные поля: `sku,date,units,sell_price`.

Опционально: `category,competitor_price,min_price,max_price,lead_time_days`.
Если в один день несколько чеков, количество суммируется, а цена усредняется с
весом по количеству.

### `purchase_costs.csv` — ERP/закупки

Обязательные поля: `sku,date,purchase_cost`.

`date` — дата начала действия фактической net-себестоимости. Опционально:
`supplier_id,purchase_order_id`. Для дня продажи применяется последняя известная
цена на этот день. Строки без предшествующей закупочной цены не оптимизируются.

### `daily_stock.csv` — WMS

Обязательные поля: `sku,date,stock`.

Опционально: `warehouse_id,on_order`. Остатки нескольких складов за день
суммируются. По умолчанию допускается снимок не старше семи дней; возраст
записывается в `stock_staleness_days`. Если свежего снимка нет, значение
помечается `missing`, даже когда технически подставлен безопасный ноль.

### `logistics_costs.csv` — TMS/бухгалтерия

Обязательные поля: `sku,date`.

Поддерживаемые фактические компоненты на единицу:

- `shipping_cost`;
- `handling_cost`;
- `storage_cost_per_unit_day`.

Транспорт и обработку можно передать общими суммами:
`shipping_total,handling_total,shipment_quantity` — сервис сам распределит их
на единицу. Для периода продаж применяется последняя фактическая поставка не
старше 180 дней. Отсутствующий компонент остается `missing`; он не считается
фактическим нулем.

Запуск:

```bash
python cli.py optimize-operational \
  --sales sales.csv \
  --purchase-costs purchase_costs.csv \
  --daily-stock daily_stock.csv \
  --logistics-costs logistics_costs.csv \
  --output data/results/operational
```

API-аналог: `POST /v1/optimize/operational-upload`, имена multipart-полей:
`sales`, `purchase_costs`, `daily_stock`, `logistics_costs`.

## Альтернативный контракт: готовая canonical CSV

Одна строка — наблюдение по SKU за период. Период может быть днем, неделей или
месяцем, но должен быть одинаковым внутри одного запуска.

## Обязательные колонки

| Колонка | Тип | Смысл |
| --- | --- | --- |
| `sku` | string | уникальный код товара |
| `date` | date | дата периода |
| `units` | float >= 0 | проданное количество |
| `sell_price` | float > 0 | фактическая средняя цена продажи за единицу |
| `purchase_cost` | float > 0 | фактическая net закупочная цена после скидок |
| `shipping_cost` | float >= 0 | входящая логистика на единицу |
| `stock` | float >= 0 | доступный остаток на конец периода |

## Рекомендуемые колонки

| Колонка | Default | Смысл |
| --- | --- | --- |
| `category` | `unknown` | категория для pooling эластичности |
| `handling_cost` | 0 | приемка/комплектация на единицу |
| `storage_cost_per_unit_day` | 0 | хранение единицы в день |
| `on_order` | 0 | уже заказано, но не принято |
| `lead_time_days` | 14 | срок поставки |
| `competitor_price` | null | сопоставленная цена конкурента |
| `min_price` | null | юридический/договорный floor |
| `max_price` | null | договорный/ценовой ceiling |
| `purchase_cost_source` | `actual` | `actual/estimated/missing` |
| `stock_source` | `actual` | происхождение остатка |
| `shipping_cost_source` | `actual` | происхождение перевозки |
| `handling_cost_source` | `missing` | происхождение обработки |
| `storage_cost_source` | `missing` | происхождение хранения |

CSV должен быть UTF-8 (допускается CP1251), разделитель — запятая, десятичный
разделитель — точка или запятая.

Пример:

```csv
sku,date,units,sell_price,purchase_cost,shipping_cost,stock,category,handling_cost,storage_cost_per_unit_day,on_order,lead_time_days,competitor_price,min_price,max_price,purchase_cost_source,stock_source,shipping_cost_source,handling_cost_source,storage_cost_source
A-001,2026-07-01,17,1290,760,54,35,auto_parts,12,0.85,20,9,1275,1050,1450,actual,actual,actual,actual,actual
```

## Требования к истории

- технический минимум портфеля: 20 валидных SKU-периодов;
- для оценки elasticity нужно 8+ наблюдений и минимум 3 разных цены;
- quality-gate ожидает минимум 90 daily, 26 weekly, 18 biweekly либо 12 monthly
  наблюдений на SKU;
- возвраты и отмены либо исключаются, либо передаются отдельным net units;
- промо, дефицит, out-of-stock и изменение карточки желательно хранить
  отдельными признаками в следующей итерации.

## Проверки перед production

- закупочная цена включает ретро-бонусы одинаковым способом по всем периодам;
- доставка приведена на единицу, не на весь заказ;
- НДС обрабатывается единообразно;
- stock соответствует той же локации, что sales;
- логистические счета распределены на фактически принятую партию, а не на
  плановое количество заказа;
- `actual` ставится только для поля, полученного из первичного регистра/счета;
- competitor_price сопоставлен с тем же товаром/объемом упаковки;
- нет смешения daily и monthly строк.

До оптимизации выполните:

```bash
python cli.py history-report --input my_history.csv --output history_quality.json
```

## Фактические результаты для shadow-mode

Одна строка — факт по SKU после рекомендации:

```csv
run_id,sku,actual_date,actual_sell_price,actual_units,actual_purchase_cost,actual_shipping_cost,stock_end
run_abcd1234,A-001,2026-08-01,1290,18,760,54,12
```

Обязательны первые шесть полей, кроме `actual_shipping_cost` (default 0) и
`stock_end`. `run_id` должен существовать, а SKU — входить в рекомендации этого
запуска.
