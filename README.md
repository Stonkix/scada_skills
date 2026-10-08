# SCADA: мониторинг логистической инфраструктуры предприятия

Хакатон, задание 3. ТЗ: [docs/spec/task.md](docs/spec/task.md), план: [docs/spec/plan.md](docs/spec/plan.md).

## Быстрый старт

Нужны Docker и Python 3.12.

```bash
python dev.py up      # PG+PostGIS, ClickHouse, Redis, Mosquitto, API — ждёт healthchecks
python dev.py reset   # стереть все данные и поднять заново
python dev.py down
```

На Linux/macOS то же самое: `make up`, `make reset`, `make down`.

API: http://localhost:8000/docs (Swagger). Вход: `dispatcher` / `security` / `admin`, пароль `demo`.

Локальная разработка без Docker:

```bash
python -m venv venv && venv/Scripts/pip install -e ./common -e "./api[dev]"
venv/Scripts/python dev.py test
venv/Scripts/python dev.py api
```

## Структура

| Папка | Что это | Этап |
|---|---|---|
| `common/` | Общий пакет: контракт события, ключи Redis | 0 ✅ |
| `api/` | Модульный монолит: auth, registry, layout, live, alerts (сейчас на моках) | 0 ✅ / 4 |
| `contracts/` | Сгенерированные контракты: `openapi.json`, `event.schema.json`, `ws.schema.json` | 0 ✅ |
| `deploy/` | docker-compose, конфиги, сиды (`seed/layout.geojson`) | 0 ✅ / 1 |
| `connectors/` | Приём HTTP/MQTT, адаптеры датчиков → `stream:events` | 2 |
| `simulator/` | Машины, турникеты, сценарии для демо | 2 |
| `worker/` | Стрим → ClickHouse, live-состояние в Redis, алерты | 3 |
| `mock-1c/` | Мок 1С-ЭПД | 6 |
| `web/` | Фронтенд (Vue 3) | 5 |

## Для фронта

API уже отвечает реальными по форме данными: `/objects` отдаёт план предприятия, машины ездят по дорогам, WebSocket шлёт обновления. Когда подключим настоящие хранилища, контракт не поменяется.

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

| type | data | Частота в моке |
|---|---|---|
| `vehicle` | `VehicleLive` — позиция, скорость, курс, топливо, геозона | 1 с |
| `sensor` | `SensorLive` — статус ok/warning/critical/offline и значения | 5 с |
| `zone` | `ZoneOccupancy` — людей в здании | 10 с |
| `alert` | `Alert` | 45 с |

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
