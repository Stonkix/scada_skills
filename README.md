# SCADA: мониторинг логистической инфраструктуры предприятия

Хакатон, задание 3. ТЗ: [docs/spec/task.md](docs/spec/task.md), план: [docs/spec/plan.md](docs/spec/plan.md).

## Быстрый старт

Нужны Docker и Python 3.12.

```bash
python dev.py up      # хранилища, db-init (миграции + сиды), connectors, worker, simulator, API, web
python dev.py seed    # вернуть все хранилища к демо-состоянию (без пересоздания контейнеров)
python dev.py reset   # стереть все данные и поднять заново
python dev.py down
```

На Linux/macOS то же самое: `make up`, `make reset`, `make down`.

API: http://localhost:8000/docs (Swagger). Вход: `dispatcher` / `security` / `admin`, пароль `demo`.

Локальная разработка без Docker:

```bash
python -m venv venv && venv/Scripts/pip install -e ./common -e ./db -e "./api[dev]"
venv/Scripts/python dev.py test
venv/Scripts/python dev.py api
```

## Структура

| Папка | Что это | Этап |
|---|---|---|
| `common/` | Общий пакет: контракт события, каталог типов датчиков, enum'ы, ключи Redis | 0 ✅ |
| `db/` | Хранилища: модели Postgres + Alembic, схема ClickHouse, Redis-группы, демо-сиды | 1 ✅ |
| `api/` | Модульный монолит: auth, registry, layout, live, alerts, kpi — на настоящих хранилищах | 4 ✅ |
| `contracts/` | Сгенерированные контракты: `openapi.json`, `event.schema.json`, `ws.schema.json` | 0 ✅ |
| `deploy/` | docker-compose, конфиги, план (`seed/layout.geojson`) и парк машин (`seed/fleet.json`) | 0 ✅ |
| `connectors/` | Приём HTTP/MQTT, API-ключи, лимиты, адаптеры датчиков → `stream:events` | 2 ✅ |
| `simulator/` | Машины по дорогам, турникеты, климат, движение, сценарии для демо | 2 ✅ |
| `worker/` | Стрим → ClickHouse, live-состояние в Redis, геозоны, правила и тревоги | 3 ✅ |
| `mock-1c/` | Мок 1С-ЭПД | 6 |
| `web/` | Фронтенд: карта, тревоги, KPI, replay, реестр (Vue 3 + Leaflet), [web/README.md](web/README.md) | 5 ✅ |

## Сервисы

| Сервис | Адрес | Что это |
|---|---|---|
| **Интерфейс** | **http://localhost:8080** | вход: `dispatcher` / `security` / `admin`, пароль `demo` |
| API | http://localhost:8000/docs | для фронта (JWT; в Swagger — кнопка Authorize) |
| Connectors | http://localhost:8001/docs | приём данных с датчиков, каталог форматов `GET /adapters` |
| Simulator | http://localhost:8010/docs | живое предприятие и «чит-меню» сценариев |

## Коннекторы: как подключить датчик

Проще всего — страница **«Коннекторы»** в интерфейсе (http://localhost:8080/connectors): выбор формата, привязка к датчику из реестра, выпуск API-ключа (админ), отправка тестового показания с подтверждением, что оно дошло до карты, готовые команды `curl` / `mosquitto_pub` / Python и последние отклонённые сообщения с причинами. Ключи: `GET/POST /connectors/keys`, `DELETE /connectors/keys/{id}` в API (право `connectors:manage`); ключ показывается один раз, хранится SHA-256.

Вручную:

HTTP: `POST /ingest/{adapter}`, заголовок `X-API-Key`, тело — объект или массив. MQTT: топик `sensors/{adapter}/{device_id}`, ключ — в MQTT 5 user property `x-api-key`.

```bash
curl -X POST localhost:8001/ingest/climate -H "X-API-Key: sk_vendordemo_8c1e2a9f4b7d4e01b5a6c3d2e1f09a7b" -H "Content-Type: application/json" -d '{"device": "clim-wh1-storage", "temperature": 64.4, "unit": "F", "humidity": 48, "ts_ms": 1791450000000}'
```

Адаптеры: `native` (наш контракт как есть), `anpr`, `skud`, `gnss` (lat/lon → метры плана), `motion`, `climate` (°F → °C). Новый формат производителя — один класс в [adapters.py](connectors/scada_connectors/adapters.py).

Коннектор проверяет ключ, лимит событий в минуту, что датчик есть в реестре, включён и его тип совпадает с адаптером. Он же подставляет координаты и зону неподвижных датчиков. Всё отклонённое уходит в `stream:dlq` с причиной (`unknown_sensor`, `type_mismatch`, `invalid_payload`, `clock_skew`, `forbidden_type`, `topic_mismatch`…). Повторная доставка того же сообщения получает тот же `event_id`.

## Симулятор и сценарии

Грузовики заезжают через КПП-1 (камера видит номер), едут к рампам, разгружаются и уезжают. Погрузчики ходят между складами, люди проходят через турникеты по времени суток, климат колеблется вокруг номинала (датчики цехов шлют °F). ГЛОНАСС, климат и движение идут по MQTT, камеры и СКУД — по HTTP; всё только через коннекторы.

```bash
curl -X POST localhost:8010/scenario/overheat
```

| Сценарий | Что происходит |
|---|---|
| `breakdown` | грузовик глохнет посреди проезда на 10 мин |
| `speeding` | служебная машина едет 38 км/ч |
| `unknown_plate` | грузовик с номером вне базы заезжает через КПП-1 |
| `unknown_card` | неизвестный пропуск (отказ) и просроченный (контроллер пропустил) |
| `overheat` | холодный склад нагревается на 7 °C |
| `after_hours` | движение на складе при пустом здании |
| `sensor_offline` | датчик климата замолкает на 10 мин |
| `demo` | всё подряд для показа |

`GET /status` — где машины, сколько людей в зданиях, активные эффекты; `POST /scenarios/stop` — вернуть всё в норму. Сценарии описаны в [simulator/scenarios.yaml](simulator/scenarios.yaml).

## Worker: обработка потока и тревоги

Читает `stream:events` группой потребителей пачками до 2000 событий или 1 с:

1. отбрасывает повторы по `event_id` (Redis `seen:event:*`, час);
2. определяет геозону машины (point-in-polygon, STRtree) и пишет входы/выходы в `zone_events`;
3. пишет пачку в ClickHouse; при сбое повторяет с паузой, сообщения остаются неподтверждёнными;
4. обновляет live-состояние в Redis и публикует изменения в `live:{здание}:{слой}` в формате WebSocket API;
5. прогоняет правила и подтверждает пачку (`XACK`).

Статистика: `redis-cli hgetall worker:stats` (обработано, повторы, отставание, время вставки, открытые тревоги).

| Правило | Когда срабатывает | Закрывается |
|---|---|---|
| `rule-threshold` | метрика вне нормы дольше 30 с; critical — за критической границей | значение вернулось в норму с запасом 5 % (гистерезис) |
| `rule-speed` | скорость > 20 км/ч дольше 5 с, critical > 30 | скорость ≤ 18 |
| `rule-whitelist-plate` | въезд через КПП-1 с номером не из списка | вручную |
| `rule-whitelist-card` | пропуск не найден / просрочен (critical, если контроллер пропустил), нет доступа в здание, вне графика | вручную |
| `rule-after-hours` | движение дольше 20 с, а по СКУД в здании никого | 5 мин без такого движения |
| `rule-breakdown` | техника стоит с заглушенным двигателем 3 мин вне стоянки/рамп | поехала |
| `rule-offline` | климат/движение молчат 5 мин, трекер — 2 мин | датчик снова на связи |
| `rule-geozone-garage` | грузовик въехал во двор ремзоны | выехал |

Одна живая тревога на пару «правило + объект»: повторы обновляют `last_seen_at`, рост до critical обновляет тревогу. Неподтверждённые тревоги эскалируются каждые `escalate_after_s` (до уровня 3). Каждая тревога хранит `rule_id` + `rule_version`. Переходы пишутся в ClickHouse `alerts_log`, тревоги публикуются в `stream:alerts` (для ВК-бота) и в WebSocket-канал.

## Хранилища

| Что | Где | Порт на хосте |
|---|---|---|
| Postgres 16 + PostGIS | справочники, пороги (версии), правила (версии), алерты, пользователи, ключи коннекторов | **5433** (5432 часто занят локальным Postgres) |
| ClickHouse | `telemetry` (сырьё, 30 дней), `telemetry_1m` / `telemetry_1h` (агрегаты), `alerts_log` | 8123 |
| Redis | стримы событий и алертов, live-состояние, дедуп | 6379 |

Схема Postgres — [db/scada_db/models.py](db/scada_db/models.py); после правок: `python dev.py revision "что поменял"`, проверить файл в `db/scada_db/migrations/versions/`, `python dev.py migrate`. ClickHouse — нумерованные SQL в `db/scada_db/clickhouse/`. Ключи Redis описаны в [common/scada_common/keys.py](common/scada_common/keys.py).

Демо-данные: 7 зданий, 26 зон, 67 датчиков, 15 машин, 300 пропусков, 8 правил алертов. В них специально заложены дыры для сценариев: пропуска `P-000280..289` просрочены, `P-000290..299` нет в базе, номера машины `v-truck-8` (Р135ЕК99) нет в списке допуска.

Пользователи: `dispatcher`, `dispatcher2`, `security`, `admin`, пароль `demo` (в БД — argon2id).

## Для фронта

API работает на настоящих данных: план и реестр из Postgres, live-состояние из Redis (его ведёт worker), история и аналитика из ClickHouse. По сравнению с моком контракт только расширился, существующие схемы не менялись. Новое — **везде нужен токен**.

### Авторизация

1. `POST /auth/login` `{"username": "dispatcher", "password": "demo"}` → `access_token` (15 мин), `refresh_token` (7 дней), `user`.
2. Запросы: заголовок `Authorization: Bearer <access_token>`. На 401 — `POST /auth/refresh` `{"refresh_token": ...}`, затем повторить запрос.
3. WebSocket: `ws://localhost:8000/ws/live?token=<access_token>` (браузер не умеет заголовки у WebSocket). Без токена соединение закрывается с кодом 4401.

| Право | dispatcher | security | admin |
|---|---|---|---|
| `map:view` — карта, live, тревоги (просмотр) | ✓ | ✓ | ✓ |
| `sensors:view` — реестр, история, маршруты | ✓ | ✓ | ✓ |
| `alerts:ack` — подтверждать, закрывать, комментировать | ✓ | ✓ | ✓ |
| `kpi:view` — KPI, heatmap, replay | ✓ | | ✓ |
| `people:view_pii` — ФИО в `/whitelist` без маски | | ✓ | ✓ |
| `sensors:edit`, `layout:edit` — датчики, пороги, загрузка CSV, план | | | ✓ |

Права приходят в access-токене (`perm`), по ним удобно прятать кнопки. 403 — права нет.

### Эндпоинты

| Что | Эндпоинт |
|---|---|
| Статика для карты | `GET /objects` (кэш по `layout_version`) |
| План для редактора | `GET /layout`, `POST /layout` `{base_version, geojson}` → 409, если план уже сохранил кто-то другой |
| Live | `GET /state/live?building_id=`, `WS /ws/live` |
| Датчики | `GET /sensors`, `GET /sensors/{id}`, `POST /sensors`, `PATCH /sensors/{id}`, `PUT /sensors/{id}/thresholds` (новая версия порогов) |
| Загрузка списка датчиков | `POST /sensors/bulk?dry_run=true` (multipart, CSV или JSON) — ошибки построчно, применяется всё или ничего |
| История | `GET /sensors/{id}/history?metric=&from=&to=` (до 1 ч — сырые точки, до суток — 1m, дальше — 1h) |
| Машины | `GET /vehicles`, `GET /vehicles/{id}/route?from=&to=` |
| Тревоги | `GET /alerts?status=&severity=&kind=&building_id=&from=&to=`, `POST /alerts/{id}/ack`, `/resolve`, `/comment` |
| Аналитика | `GET /kpi?from=&to=`, `GET /heatmap?cell=10&source=vehicles\|stops`, `GET /replay?from=&to=&step=5` |
| Пропуска и номера | `GET /whitelist?kind=card&q=` |

- **Типы:** `npx openapi-typescript contracts/openapi.json -o src/api/schema.ts`
- **Координаты:** метры локального плана, (0, 0) — юго-западный угол, x на восток, y на север. Размер площадки — `extent` из `/objects`. Для подложки WGS84 — `georef`.
- **Статика:** `GET /objects` одним ответом (здания, помещения, дороги, геозоны, КПП, датчики, машины). Кэшировать по `layout_version`.
- **Живые данные:** снимок `GET /state/live`, дальше `WS /ws/live`.

### WebSocket `/ws/live`

После подключения отправь фильтр (можно слать повторно; `null` или отсутствие поля — без фильтра):

```json
{"op": "subscribe", "buildings": ["b-wh1", "site"], "layers": ["vehicles", "alerts"], "sensor_types": ["climate"]}
```

`layers`: `sensors | vehicles | people | alerts`. `"site"` — объекты на улице (машины).

Сервер шлёт сообщения с полем `type`:

| type | data | Когда приходит |
|---|---|---|
| `vehicle` | `VehicleLive` — позиция, скорость, курс, топливо, геозона | каждый замер трекера (~2 с) |
| `sensor` | `SensorLive` — статус ok/warning/critical/offline и значения | каждое показание датчика |
| `zone` | `ZoneOccupancy` — людей в здании | каждый проход через СКУД |
| `alert` | `Alert` | открытие, рост критичности, эскалация, подтверждение, закрытие |

Схемы — в `contracts/ws.schema.json`.

## Контракт события датчика

Все датчики приводятся коннекторами к одному формату ([common/scada_common/events.py](common/scada_common/events.py), JSON Schema — `contracts/event.schema.json`):

```json
{
  "event_id": "uuid — ключ идемпотентности",
  "schema_v": 1,
  "sensor_id": "clim-wh2-storage",
  "type": "climate",
  "ts": "2026-10-08T12:00:00+03:00",
  "received_at": "ставит коннектор",
  "geo": {"x": 470, "y": 365, "floor": 1},
  "zone_id": "r-wh2-storage",
  "payload": {"temperature_c": 4.2, "humidity_pct": 61}
}
```

Типы и payload: `anpr_camera` (plate, direction, confidence), `access_control` (card_id, direction, granted), `gnss` (speed_kmh, heading_deg, fuel_pct, odometer_km, engine_on), `motion` (detected), `climate` (temperature_c, humidity_pct).

После изменения схем: `python dev.py contracts`.
