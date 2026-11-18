# Математика модели

## 1. Прогноз спроса

Глобальная `HistGradientBoostingRegressor` обучается сразу на всех SKU на
`log1p(units)`. Признаки:

- код SKU и категории;
- индекс периода;
- синус/косинус месяца;
- lag 1, lag 2;
- rolling mean за 3 предыдущих периода.

Time split сохраняет порядок дат. В ответе публикуются WAPE и MAE. Будущий путь
строится рекурсивно; интервал P10/P90 рассчитывается по SKU residual scale.

Глобальная модель полезна cold/sparse SKU, но не заменяет backtest на данных
клиента. При intermittent demand следующая версия может добавить Croston/TSB и
probabilistic boosting.

## 2. Эластичность

Если есть минимум 8 наблюдений и 3 разные цены, Ridge оценивает:

```text
log(1 + units) =
    β0 + βp·log(price / competitor_price)
       + βt·trend + βs·seasonality + error
```

`βp` ограничивается экономически допустимым диапазоном `[-4.0, -0.1]`.
Положительная observational-эластичность не принимается автоматически:
вероятна эндогенность/промо. Тогда используется медиана категории или global
fallback `-1.25`, а SKU получает `low confidence`.

При candidate price `p`:

```text
demand(p) = base_forecast · (p / current_price) ^ elasticity
```

## 3. Landed cost

```text
capital_cost =
    purchase_cost · key_rate · lead_time_days / 365

landed_cost =
    purchase_cost
  + shipping_cost
  + handling_cost
  + storage_cost_per_unit_day · lead_time_days
  + capital_cost
```

Каждый компонент имеет независимый provenance. Число может участвовать в
shadow-сценарии при `estimated`, но рекомендация не проходит quality-gate для
`approval/auto`, пока закупочная цена, ежедневный остаток, перевозка, обработка
и хранение не имеют статус `actual`.

Минимальная экономическая цена:

```text
economic_floor = landed_cost / (1 - required_margin)
```

После этого применяются договорные min/max и лимит изменения относительно
текущей цены. Если текущая цена уже ниже экономического floor, floor имеет
приоритет, а рекомендация получает флаг
`price_floor_above_change_guardrail`.

## 4. Совместная оптимизация цены и заказа

Для 61 candidate price генерируется Monte Carlo demand. Заказ:

```text
order_qty =
    max(0, quantile(demand, service_level) - stock - on_order)
```

Прибыль каждого сценария:

```text
profit =
    (sell_price - landed_cost) · sold
  - storage_cost · horizon_days · leftover
  - lost_sale_penalty
```

Целевая функция штрафует downside:

```text
objective =
    expected_profit
  - risk_aversion · (expected_profit - profit_P10)
```

Это не GA: для одномерного price corridor детерминированная сетка надежнее,
быстрее и воспроизводима. GA полезен при большом дискретном portfolio constraint
space, но прежний проект оптимизировал лишь один общий multiplier.

## 5. Закупочная цена

Максимально допустимая закупочная цена:

```text
walk_away =
    recommended_sell_price · (1 - required_margin)
  - non_purchase_logistics_cost
  - inventory_risk_buffer
```

Переговорный target выбирается ниже walk-away с учетом текущей цены поставщика
и базового сценария cost growth. Система никогда не рекомендует добровольно
повышать закупочную цену выше текущей.

## 6. Почему это все еще не causal pricing

Historical price часто коррелирует с промо, дефицитом и решениями менеджера.
Поэтому regression elasticity — screening estimate. Для автопубликации нужны:

1. holdout SKU;
2. switchback/A-B price test;
3. измерение contribution margin, lost sales и inventory days;
4. ограничения РРЦ/MAP/регулирования;
5. human approval до накопления causal evidence.

## 7. Quality-gates и shadow-mode

До формирования workflow анализируется каждый SKU:

- 90 daily, 26 weekly, 18 biweekly либо 12 monthly наблюдений;
- минимум 3 ценовых уровня для SKU-elasticity;
- фактическая, а не восстановленная закупочная цена;
- признаки intermittent demand и возможного stockout-censoring.

`shadow` всегда присваивает `shadow_observation` и `published=false`.
`approval` допускает решение оператора только при WAPE не выше заданного
порога и прошедшей истории SKU. `auto` добавляет более строгий WAPE, high/medium
elasticity confidence, лимит изменения цены, положительную стресс-прибыль и
независимый kill switch.

Фактические продажи связываются с `run_id` и SKU. Shadow-отчет показывает
forecast error и фактическую contribution margin. Модельный expected uplift
сохраняется только как proxy с `causal_uplift_claim=false`.
