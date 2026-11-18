# Production runbook SupplyMargin AI SaaS v1.4

## 1. Рекомендуемый сервер

Для первых платящих пользователей: Linux VDS, 4 vCPU, 8 ГБ RAM, 80 ГБ NVMe.
GPU не нужен. Production Compose распределяет ресурсы так:

- API: до 1 vCPU / 1 ГБ;
- ML-worker: до 2 vCPU / 4 ГБ;
- PostgreSQL, MinIO и Caddy используют оставшийся ресурс;
- одновременно работает один ML-worker.

Для пилота API и worker можно развернуть отдельно в Container Apps, PostgreSQL
заменить управляемой БД, а MinIO — Cloud.ru Object Storage.

## 2. Подготовка домена

1. Создайте A-запись домена на публичный IPv4 VDS.
2. Откройте входящие TCP-порты 80 и 443, UDP 443.
3. Не открывайте наружу 5432, 9000 и 9001.
4. Дождитесь обновления DNS.

Caddy самостоятельно запросит TLS-сертификат после запуска.

## 3. Подготовка `.env`

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Полученное значение вставьте в `SUPPLYMARGIN_JWT_SECRET`. Отдельно создайте
случайные пароли PostgreSQL, MinIO и admin API.

Обязательные значения:

```dotenv
DOMAIN=app.example.ru
SUPPLYMARGIN_JWT_SECRET=<64 случайных символа>
POSTGRES_PASSWORD=<случайный пароль>
MINIO_ROOT_USER=supplymargin
MINIO_ROOT_PASSWORD=<случайный пароль, минимум 16 символов>
SUPPLYMARGIN_ADMIN_API_KEY=<случайный серверный ключ>
SUPPLYMARGIN_YOOKASSA_SHOP_ID=<shopId>
SUPPLYMARGIN_YOOKASSA_SECRET_KEY=<secretKey>
SUPPLYMARGIN_YOOKASSA_RETURN_URL=https://app.example.ru/?payment=return
```

Файл `.env` не отправляется в Git и не включается в Docker-образ.

## 4. Первый запуск

```bash
docker compose -f docker-compose.production.yml config
docker compose -f docker-compose.production.yml up --build -d
docker compose -f docker-compose.production.yml ps
docker compose -f docker-compose.production.yml logs --tail 100 api worker caddy
```

Проверки:

```bash
curl https://app.example.ru/healthz
curl https://app.example.ru/readyz
```

Ожидается HTTP 200 и `database: true`, `object_storage: true` в `/readyz`.

## 5. ЮKassa

В личном кабинете ЮKassa укажите URL HTTP-уведомлений:

```text
https://app.example.ru/v1/billing/webhook
```

Подпишите события:

- `payment.succeeded`;
- `payment.canceled`.

Доступ включается только после того, как backend повторно запросит платеж у
ЮKassa, проверит статус, пользователя, тариф и сумму. Возврат пользователя по
`return_url` не активирует подписку самостоятельно.

До подключения магазина можно выдать доступ вручную:

```bash
curl -X POST "https://app.example.ru/v1/admin/subscriptions/user@example.ru" \
  -H "X-Admin-Key: <SUPPLYMARGIN_ADMIN_API_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"plan_code":"basic","days":30}'
```

## 6. Хранение данных

Встроенный production Compose использует приватный MinIO. Структура:

```text
users/{user_id}/datasets/{dataset_id}/v001/source.xlsx
users/{user_id}/datasets/{dataset_id}/v001/normalized.csv
users/{user_id}/datasets/{dataset_id}/runs/{run_id}/model.joblib
users/{user_id}/datasets/{dataset_id}/runs/{run_id}/model_manifest.json
users/{user_id}/datasets/{dataset_id}/runs/{run_id}/result.json
users/{user_id}/datasets/{dataset_id}/runs/{run_id}/recommendations.xlsx
```

Для Cloud.ru Object Storage задайте:

```dotenv
SUPPLYMARGIN_STORAGE_BACKEND=s3
SUPPLYMARGIN_S3_ENDPOINT_URL=<S3 endpoint Cloud.ru>
SUPPLYMARGIN_S3_REGION=<регион>
SUPPLYMARGIN_S3_BUCKET=supplymargin-production
SUPPLYMARGIN_S3_ACCESS_KEY_ID=<key id>
SUPPLYMARGIN_S3_SECRET_ACCESS_KEY=<key secret>
SUPPLYMARGIN_S3_SSE=AES256
```

Удалите из блоков `api.environment` и `worker.environment` в production Compose
переменные, принудительно указывающие `http://minio:9000`, после чего сервисы
будут использовать значения `.env`.

## 7. Резервные копии

Минимальный график: PostgreSQL ежедневно, файлы — ежедневно или с включенным
versioning в объектном хранилище.

Пример PostgreSQL backup:

```bash
mkdir -p backups
docker compose -f docker-compose.production.yml exec -T postgres \
  pg_dump -U supplymargin -d supplymargin -Fc > backups/supplymargin_$(date +%F).dump
```

Восстановление сначала проверяйте на отдельном staging-сервере.

## 8. Обновление

```bash
docker compose -f docker-compose.production.yml build api worker
docker compose -f docker-compose.production.yml up -d api worker
docker compose -f docker-compose.production.yml logs --tail 100 api worker
```

Перед обновлением сделайте backup PostgreSQL. Старые `model.joblib` могут быть
несовместимы с новой major-версией scikit-learn, поэтому сохраняются исходная и
нормализованная таблицы: модель всегда можно переобучить.

## 9. Мониторинг

Endpoints:

- `/healthz` — процесс API;
- `/readyz` — БД, объектное хранилище и служебные данные;
- `/metrics` — Prometheus.

Контролируйте:

- HTTP 4xx/5xx и p95 latency;
- RAM/CPU worker;
- количество `queued`, `running`, `failed`;
- WAPE, MAE и долю low-confidence SKU;
- свободное место PostgreSQL/Object Storage;
- срок действия TLS;
- успешность backup.

Worker при запуске возвращает в очередь задачи, которые находились в состоянии
`running` более двух часов — это защищает от зависания после аварийного restart.

## 10. Безопасность и персональные данные

- production не запустится с коротким или шаблонным JWT secret;
- refresh token хранится в Secure HttpOnly cookie и ротируется;
- пароли хешируются Argon2;
- все запросы к данным фильтруются по `user_id` из JWT;
- чужой dataset/run возвращает 404;
- MinIO/PostgreSQL не публикуются в интернет;
- Caddy выставляет HTTPS и security headers;
- `.xlsm`, `.xls` и неизвестные форматы отклоняются;
- тариф ограничивает размер файла, число таблиц и число расчетов;
- удаление аккаунта удаляет SQL-данные и пользовательский префикс файлов.

Перед публичным запуском подготовьте пользовательское соглашение, политику
обработки персональных данных, согласия, сроки хранения и юридически проверьте
необходимость уведомления Роскомнадзора. Реальные клиентские файлы не
загружайте в GitHub, Lovable или тестовые зарубежные сервисы.

## 11. Безопасный режим ML

Оставьте `SUPPLYMARGIN_MODE=shadow` минимум на полный цикл поставки и несколько
недель фактических продаж. В этом режиме сервис формирует рекомендации, но не
публикует цены во внешнюю ERP.

Последовательность:

1. `shadow` и сбор outcomes;
2. ручная проверка рекомендаций;
3. A/B или switchback-тест;
4. `approval` на ограниченной группе SKU;
5. `auto` только после отдельного адаптера публикации, kill switch и rollback.

Ожидаемый прирост в модели — расчетный proxy, а не финансовая гарантия.
