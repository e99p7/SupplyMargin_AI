# SupplyMargin AI SaaS v1.4

Многопользовательский SaaS для прогнозирования спроса и совместной оптимизации
закупочной, розничной цены и запаса с учетом логистической себестоимости.

Версия 1.4 добавляет к ML-ядру готовый сайт и личный кабинет, регистрацию и
JWT-сессии, изоляцию клиентов, подписки ЮKassa, версии CSV/XLSX, постоянное
S3-хранилище, PostgreSQL, фоновый worker и сохранение обученных моделей.

Состояние рыночного снимка в комплекте: **25.07.2026**.

## Что это за продукт

Обычная TMS оптимизирует рейсы, WMS — операции склада, а системы динамического
ценообразования — цену продажи. SupplyMargin AI замыкает разрыв между ними:

1. прогнозирует спрос отдельно по каждому SKU;
2. оценивает ценовую эластичность SKU, а при нехватке данных использует
   иерархический fallback категории;
3. считает полную landed cost: закупка, перевозка, обработка, хранение в lead
   time и стоимость капитала;
4. совместно выбирает безопасную цену продажи и объем заказа;
5. выдает закупщику **целевую цену переговоров** и **максимально допустимую
   закупочную цену**;
6. проверяет решение в базовом, позитивном и стрессовом сценариях.
7. работает в безопасном `shadow` / `approval` / `auto` workflow, причем
   автопубликация выключена отдельным kill switch;
8. измеряет качество истории до расчета и сравнивает прогноз с последующим
   фактом продаж.
9. объединяет отдельные выгрузки ERP, WMS и TMS без фиктивного join между
   разными компаниями;
10. хранит provenance каждого компонента затрат: `actual`, `estimated` или
    `missing`.

Важно: у закупочной цены нет внутреннего математического «оптимума» — чем она
ниже, тем лучше для покупателя. Поэтому честный результат системы — не
«волшебная точная цена рынка», а переговорный target и экономически обоснованный
walk-away ceiling.

## Быстрый старт без Docker

Требуется Python 3.11–3.13.

```bash
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python cli.py demo --horizon 3
uvicorn supplymargin.api:app --host 0.0.0.0 --port 8000
# В отдельном терминале:
python -m supplymargin.worker
```

Откройте:

- Dashboard: `http://localhost:8000/`
- OpenAPI / Swagger: `http://localhost:8000/docs`
- Healthcheck: `http://localhost:8000/healthz`

Результаты demo сохраняются в `data/results/`, а пользовательские файлы — в
`data/objects/`. Для локальной проверки подписку можно выдать через admin API,
если в `.env` установлен `SUPPLYMARGIN_ADMIN_API_KEY`.

## Быстрый запуск через Docker

```bash
cp .env.example .env
# задайте длинный SUPPLYMARGIN_JWT_SECRET в .env
docker compose up --build -d
curl http://localhost:8000/healthz
```

Локальный Compose использует SQLite и общее файловое хранилище. Он подходит для
демонстрации и одного worker.

## Production на VDS

Production Compose включает PostgreSQL, приватный MinIO, API, отдельный
ML-worker и Caddy с автоматическим HTTPS:

```bash
cp .env.example .env
# заполните DOMAIN, JWT, PostgreSQL, MinIO и ЮKassa секреты
docker compose -f docker-compose.production.yml up --build -d
docker compose -f docker-compose.production.yml ps
```

Для Cloud.ru Object Storage вместо MinIO укажите S3 endpoint/ключи и удалите
S3-overrides из `docker-compose.production.yml`. Подробная последовательность:
[production runbook](docs/production_runbook_ru.md).

### Windows и Docker daemon

Docker daemon входит в Docker Desktop и не может быть встроен внутрь Python-
проекта как обычная библиотека. В версии 1.4 установка и запуск автоматизированы.

Распакуйте архив и запустите двойным кликом:

```text
INSTALL_AND_RUN_WINDOWS.cmd
```

Скрипт проверит виртуализацию и WSL 2, установит Docker Desktop через WinGet,
если он отсутствует, запустит daemon, дождется `Engine running`, проверит режим
Linux containers и выполнит `docker compose up --build -d`.

Если Docker Desktop уже установлен, можно использовать прежние диагностические
команды:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\check_docker_windows.ps1
.\scripts\start_windows.ps1
```

После первой установки WSL 2 Windows может потребовать одну перезагрузку. Перед
установкой Docker Desktop скрипт показывает ссылку на условия и просит явно
ввести `YES`; без подтверждения установка прекращается. При первом старте
Docker Desktop может показать дополнительное соглашение в интерфейсе.

Подробная диагностика: [Docker на Windows](docs/windows_docker_ru.md).

## Команды

```bash
# Полный demo на реальной публичной истории цен/спроса
python cli.py demo --horizon 3

# Свой CSV
python cli.py optimize --input my_history.csv --horizon 14

# Реальная схема из четырех выгрузок
python cli.py optimize-operational \
  --sales sales.csv \
  --purchase-costs purchase_costs.csv \
  --daily-stock daily_stock.csv \
  --logistics-costs logistics_costs.csv \
  --output data/results/operational

# Только объединить и проверить данные, без расчета рекомендаций
python cli.py integrate-data \
  --sales sales.csv \
  --purchase-costs purchase_costs.csv \
  --daily-stock daily_stock.csv \
  --logistics-costs logistics_costs.csv \
  --output data/integrated

# Проверка, каких данных не хватает по каждому SKU
python cli.py history-report --input my_history.csv --output history_quality.json

# После периода наблюдения: загрузить факт и получить shadow-отчет
python cli.py record-outcomes --input outcomes.csv
python cli.py shadow-report --run-id run_...

# Обновить официальный макроснимок Банка России
python cli.py refresh-market

# Тесты
pytest -q
```

## SaaS API

Обычному пользователю команды не нужны: весь поток доступен в браузере. Для
проверки API зарегистрируйтесь, активируйте тестовый период или выдайте подписку
admin-командой, затем передавайте access token в `Authorization: Bearer ...`.

```bash
curl -X POST http://localhost:8000/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"owner@example.ru","password":"StrongPassword123"}'
```

После входа UI сам загружает таблицу, получает `dataset_version_id` и создает
фоновый расчет через `/v1/runs`.

## Legacy API

Эти методы предназначены для старой server-to-server интеграции. В production
они отключены, если `SUPPLYMARGIN_API_KEY` не задан; ключ нельзя помещать в
браузерный JavaScript.

```bash
curl -X POST http://localhost:8000/v1/demo/run \
  -H "X-API-Key: $SUPPLYMARGIN_API_KEY"

curl -X POST http://localhost:8000/v1/optimize/upload \
  -H "X-API-Key: $SUPPLYMARGIN_API_KEY" \
  -F "file=@my_history.csv" \
  -F "horizon=14"

curl -X POST http://localhost:8000/v1/history/quality/upload \
  -H "X-API-Key: $SUPPLYMARGIN_API_KEY" \
  -F "file=@my_history.csv"
```

Основные SaaS endpoints:

| Метод | URL | Назначение |
| --- | --- | --- |
| GET | `/healthz` | liveness |
| GET | `/readyz` | readiness и доступность storage |
| GET | `/v1/market` | текущий рыночный снимок |
| GET | `/v1/system/mode` | режим, kill switch и статус публикации |
| POST | `/v1/auth/register` | регистрация пользователя |
| POST | `/v1/auth/login` | вход и JWT |
| POST | `/v1/auth/refresh` | обновление сессии через HttpOnly cookie |
| GET | `/v1/billing/status` | состояние подписки |
| POST | `/v1/billing/checkout` | создание платежа ЮKassa |
| POST | `/v1/billing/webhook` | подтверждение оплаты ЮKassa |
| POST | `/v1/datasets` | загрузка CSV/XLSX |
| POST | `/v1/datasets/{id}/versions` | новая версия таблицы |
| POST | `/v1/runs` | поставить оптимизацию в очередь |
| GET | `/v1/runs/{id}` | статус фонового расчета |
| GET | `/v1/runs/{id}/result` | полный JSON-результат |
| GET | `/v1/runs/{id}/download` | скачать Excel |
| POST | `/v1/demo/run` | запуск комплектного demo |
| POST | `/v1/optimize` | оптимизация массива JSON |
| POST | `/v1/optimize/upload` | оптимизация CSV |
| POST | `/v1/optimize/operational-upload` | ERP + WMS + TMS четырьмя CSV |
| POST | `/v1/history/quality/upload` | аудит полноты истории CSV |
| POST | `/v1/outcomes` | регистрация фактических продаж |
| GET | `/v1/shadow/report/{run_id}` | forecast-vs-actual shadow-отчет |
| POST | `/v1/decisions` | approve/reject/override вне shadow-mode |
| GET | `/v1/legacy/runs` | старые server-to-server запуски |
| GET | `/metrics` | Prometheus metrics |

## Реальные данные

В `data/demo/retail_price_history.csv` находится реальный публичный
e-commerce датасет из прежнего проекта пользователя: 676 помесячных наблюдений,
товарные категории, продажи, цены, доставка и цены конкурентов.

Самый близкий открытый источник — база крупного китайского супермаркета:
28 757 SKU, ежедневные продажи, остатки, закупочные цены и 174 155 пополнений.
Пять исходных XLSX опубликованы как Supporting Information к DOI
`10.1002/nav.21957`. Они не содержат фактические счета за перевозку, обработку
и хранение, поэтому такой контур остается `estimated/missing`, а не выдается за
реальную логистику. Загрузчик открытых файлов:

```bash
python scripts/download_supermarket_wiley.py
```

Wiley может потребовать ручное скачивание через браузер; скрипт в этом случае
выдаст точную страницу и имена пяти файлов. Подробности:
[открытые данные и ограничения](docs/open_data_ru.md).

Комплектное demo использует реальную публичную историю продаж, цен, freight и
цен конкурентов. Производные `purchase_cost`, `stock`, `handling` и `storage`
помечены построчно. Для промышленного решения передайте четыре связанные
выгрузки по контракту из `docs/data_contract_ru.md`.

Сервис не обещает базу «всех существующих товаров»: такой полной и достоверной
базы в мире нет. Он масштабируется на любое количество SKU, которые бизнес
передаст по CSV или API. Для дополнительной реальной истории предусмотрен
импортер официального UCI Online Retail (541 909 транзакций).

## Структура

```text
SupplyMargin_AI/
├── supplymargin/                 # модель, оптимизатор, API, интеграции
│   ├── routers/                  # auth, billing, datasets, runs
│   ├── models.py                 # SaaS-модели SQLAlchemy
│   ├── object_storage.py         # local/S3 хранилище
│   ├── worker.py                 # фоновая очередь ML
│   └── integration.py            # join ERP/WMS/TMS с provenance
├── data/demo/                    # реальная история + макроснимок
├── data/results/                 # результаты запусков
├── docs/                         # анализ рынка, интеграция, runbook
├── notebooks/                    # Google Colab
├── scripts/                      # импорт UCI и сборка notebook
├── INSTALL_AND_RUN_WINDOWS.cmd   # установка Docker + daemon + запуск
├── tests/                        # unit + API tests
├── cli.py
├── Dockerfile
├── docker-compose.yml             # локальный запуск
└── docker-compose.production.yml  # PostgreSQL + MinIO + Caddy
```

## Ограничения, которые нельзя скрывать

- Рекомендации становятся надежными после минимум 8–12 ценовых периодов на SKU.
- Наблюдательная эластичность не доказывает причинность. Для дорогих решений
  используйте A/B или switchback-тесты.
- РРЦ, регулируемые цены, MAP и договорные ограничения должны быть переданы как
  guardrails.
- Автопубликацию цены включайте только после shadow-mode, backtest и лимита
  изменения цены; по умолчанию сервис лишь рекомендует.
- Для будущего `approval` по SKU нужны фактическая закупочная цена, ежедневный
  остаток и полный фактический логистический контур. Пробелы не превращаются в
  нули с меткой `actual`.
- Demo — воспроизводимый технический пример, не финансовая гарантия.

Подробнее:

- [Конкурентный анализ](docs/market_analysis_ru.md)
- [Математика модели](docs/algorithm_ru.md)
- [Контракт данных](docs/data_contract_ru.md)
- [Открытые реальные данные](docs/open_data_ru.md)
- [Интеграция с сайтом](docs/integration_ru.md)
- [Production runbook](docs/production_runbook_ru.md)
- [Docker daemon на Windows](docs/windows_docker_ru.md)

## Важно: сохранение аккаунтов между версиями

Локальная база по умолчанию находится в `data/results/supplymargin_saas.sqlite3` относительно каталога проекта. При распаковке новой версии в другую папку получится другая база, и старый аккаунт не будет найден. Чтобы сохранить пользователей между обновлениями, задайте в `.env` постоянный `SUPPLYMARGIN_DATA_DIR` (например, абсолютный путь к прежней папке `data`) либо `SUPPLYMARGIN_DATABASE_URL` с прежним SQLite-файлом. Не удаляйте и не перезаписывайте старый файл базы. Сообщение «Неверная почта или пароль» может означать, что приложение подключено не к той базе; сначала проверьте эти переменные и путь к файлу.

Команда `python cli.py demo --horizon 3` выполняет демонстрационный прогноз спроса без финансовой оптимизации: демонстрационные данные не являются подтверждёнными фактическими данными по затратам и остаткам. Для финансовых рекомендаций используйте проверенные операционные данные.
