# Интеграция с сайтом и системами

## Основной SaaS-сценарий v1.4

Отдельный конструктор для личного кабинета не требуется: сайт, регистрация,
оплата, загрузка таблиц и просмотр результатов уже встроены в приложение.
Разместите весь контейнер SupplyMargin AI на одном домене, например
`https://pricing.example.ru`. Браузер работает с API на том же домене, поэтому
JWT не нужно передавать в сторонний конструктор.

Путь пользователя:

1. регистрация через `/v1/auth/register`;
2. выбор тарифа и переход в ЮKassa через `/v1/billing/checkout`;
3. после подтвержденного webhook подписка активируется автоматически;
4. CSV/XLSX сохраняется через `/v1/datasets`, следующая загрузка может стать
   новой версией через `/v1/datasets/{id}/versions`;
5. `/v1/runs` ставит расчет в очередь, отдельный worker сохраняет модель,
   JSON-результат и Excel;
6. клиент может вернуться позже, скачать исходную версию таблицы или запустить
   по ней новый расчет.

Все запросы к таблицам и расчетам проверяют `user_id`. Оригинал, нормализованная
таблица, обученная модель и результаты находятся под приватным префиксом клиента
в local/S3-хранилище. PostgreSQL хранит пользователей, подписки, версии и
ссылки на объекты.

## Встраивание лендинга конструктора

Tilda, Битрикс24.Сайты или другой конструктор можно оставить только для
маркетинговых страниц. Кнопка «Войти» должна вести на домен приложения:

```html
<a href="https://pricing.example.ru/">Войти в SupplyMargin AI</a>
```

Не помещайте личный кабинет в cross-domain iframe: HttpOnly cookie, платежный
возврат и защитные заголовки надежнее работают на собственном домене.

## Legacy iframe (только закрытый пилот)

Подходит для Tilda, Битрикс24.Сайты, Webflow, WordPress и любого конструктора с
HTML-блоком. Сначала разместите контейнер SupplyMargin AI на своем домене,
например `https://pricing.example.ru`.

```html
<script
  src="https://pricing.example.ru/widget.js"
  data-base="https://pricing.example.ru"
  data-height="820">
</script>
```

Это старый server-to-server интерфейс с единым API key. Он оставлен для
совместимости и не является кабинетом с разделением клиентов. В production он
выключен, если `SUPPLYMARGIN_API_KEY` пуст.

## Legacy server-to-server REST

Надежнее для магазина: backend сайта передает историю/актуальное состояние и
не раскрывает API key в браузере.

```python
import requests

with open("sku_history.csv", "rb") as source:
    response = requests.post(
        "https://pricing.example.ru/v1/optimize/upload",
        headers={"X-API-Key": "SECRET"},
        files={"file": ("sku_history.csv", source, "text/csv")},
        data={"horizon": 14, "min_margin": 0.18, "service_level": 0.92},
        timeout=120,
    )
response.raise_for_status()
recommendations = response.json()["recommendations"]
```

По умолчанию `SUPPLYMARGIN_MODE=shadow`: сервис сам сохраняет рекомендации,
но не публикует их. Возвращайте последующий факт в `/v1/outcomes` и смотрите
`/v1/shadow/report/{run_id}`. После приемки переведите окружение в `approval`;
approve/reject/override сохраняются через `/v1/decisions`.

## Вариант 3: 1С / ERP / WMS

1. регламентное задание выгружает продажи и закупочную себестоимость из ERP,
   ежедневные остатки из WMS и расходы из TMS/бухгалтерии;
2. backend вызывает `/v1/optimize/operational-upload`;
3. результат записывается в регистр рекомендаций;
4. факт продаж возвращается в `/v1/outcomes` для shadow-оценки;
5. после пилота ответственный согласует изменения;
6. утвержденная цена и заказ уходят в master-system через отдельный адаптер.

В комплекте намеренно нет коннектора, меняющего master-price: это исключает
случайную публикацию. API всегда возвращает `published=false`.

Пример четырехфайлового запроса:

```python
import requests

with (
    open("sales.csv", "rb") as sales,
    open("purchase_costs.csv", "rb") as costs,
    open("daily_stock.csv", "rb") as stock,
    open("logistics_costs.csv", "rb") as logistics,
):
    response = requests.post(
        "https://pricing.example.ru/v1/optimize/operational-upload",
        headers={"X-API-Key": "SECRET"},
        files={
            "sales": ("sales.csv", sales, "text/csv"),
            "purchase_costs": ("purchase_costs.csv", costs, "text/csv"),
            "daily_stock": ("daily_stock.csv", stock, "text/csv"),
            "logistics_costs": ("logistics_costs.csv", logistics, "text/csv"),
        },
        data={"horizon": 14, "min_margin": 0.18, "service_level": 0.92},
        timeout=180,
    )
response.raise_for_status()
result = response.json()
print(result["integration_report"]["actual_coverage"])
```

Ответ возвращает не только рекомендации, но и:

- `integration_report.actual_coverage` — долю фактических полей;
- `data_lineage` — готовность данных портфеля;
- `quality_gate_status` по каждому SKU;
- отдельные причины `purchase_cost_not_actual`,
  `daily_inventory_not_actual`, `logistics_cost_not_actual`.

## JSON API

`POST /v1/optimize` принимает:

```json
{
  "records": [
    {
      "sku": "A-001",
      "date": "2026-07-01",
      "units": 17,
      "sell_price": 1290,
      "purchase_cost": 760,
      "shipping_cost": 54,
      "stock": 35,
      "category": "auto_parts"
    }
  ],
  "options": {
    "horizon": 14,
    "min_margin": 0.18,
    "max_price_change": 0.12,
    "service_level": 0.92,
    "risk_aversion": 0.25
  }
}
```

Для запроса нужно минимум 8 Pydantic-строк и фактически 20 валидных
SKU-периодов после lag generation.

Перед первым расчетом отправьте тот же CSV в
`POST /v1/history/quality/upload`. Ответ содержит оценку A–D, требуемое число
периодов и блокирующие флаги по каждому SKU.

## Безопасность

- API key только server-side;
- TLS обязателен;
- CORS — точный список доменов, не `*`;
- лимит тела запроса и rate limit на reverse proxy;
- отдельные service accounts для чтения и публикации цен;
- журналировать run_id, входной snapshot, approval и итоговую цену;
- персональные данные покупателей модели не нужны — не отправляйте их.
