## Agent skills

### Issue tracker

Issues and specs live in this repo's GitHub Issues, managed via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Project

Hackathon SCADA system for monitoring an enterprise's logistics infrastructure. Spec: `docs/spec/task.md`, staged plan: `docs/spec/plan.md`.

- Commands: `python dev.py up|down|reset|seed|contracts|test|api` (Makefile forwards to it; Windows has no make). Python deps live in `venv/`.
- `common/` is the shared event contract, sensor catalogue, enums and Redis keys; `db/` (`scada_db`) owns storage: SQLAlchemy models + Alembic, ClickHouse SQL migrations, Redis groups, demo seed (`python -m scada_db init|migrate|seed --force`); `api/` is a modular monolith (one package per module: auth, registry, layout, live, alerts, kpi, connectors, predict).
- `connectors/` (port 8001) is the only way data enters: adapters per vendor format, API keys (sha256 in `api_keys`), rate limit, registry checks, DLQ. `simulator/` (port 8010) emulates devices and must send only through connectors (MQTT for gnss/climate/motion, HTTP for anpr/skud, waybills to `POST /documents/waybill` as an emulated TMS); scenarios live in `simulator/scenarios.yaml`. Trucks run trips between sites along the public roads; every trip status change is a waybill (Postgres `trips`, API `GET /trips`, `GET /vehicles/{id}/trip`).
- Devices report by time plus on change (`scada_common.catalog.REPORTING`: GNSS every second while moving, 60 s standing; climate 120 s or ±0.5 °C; smoke 120 s or ±0.5 %/m; motion on state change). Connectors reject reports faster than `min_interval_s` (`too_frequent`); the offline rule allows 3 missed heartbeats; KPI weighs each GNSS fix by the time to the next one. The simulator's `clock` is injectable for tests. `POST /sim/time {scale}` (cheat menu) fast-forwards vehicles up to ×30; ETAs shrink accordingly. The fleet is 9 trucks + 2 loaders + 1 service car (no staff cars). Cheat-menu incidents raise their alarm at once (critical values skip the warning hold, engine off outside docks/parkings alarms at once, scenarios jump instead of ramping); `POST /sim/chaos {on, every_s}` fires random incidents across all sites while on. A waybill supersedes the vehicle's unfinished trips. The live store's `revision` counter is what to watch for live updates: its maps are mutated in place, so `watch(() => live.vehicles)` never fires.
- `worker/` consumes `stream:events`: dedup -> geozones -> ClickHouse -> live state (Redis, published in WS message format from `scada_common.live`) -> alert rules. Rules are pure functions in `worker/scada_worker/rules.py` (event context -> open/clear signals); `alerts.py` stores them (Postgres is the source of truth, Redis `alert:open:*` mirrors live ones).
- Live/alert models (`SensorLive`, `VehicleLive`, `Alert`, WS messages) live in `common/scada_common/{live,alerts}.py`; the API re-exports them. Changing them changes the frontend contract.
- `api/` runs on the real stores (no mock): Postgres via sync SQLAlchemy in plain `def` endpoints, Redis async for live state/WS, ClickHouse for history/KPI. Auth is JWT (`app/auth/security.py`); every endpoint except login/refresh/health needs a permission via `require("...")` (permissions per role live in `roles.permissions`). API tests are integration tests against the running stack.
- `POST /layout` applies the plan to the registry tables (`app/layout/sync.py`); sensors registered via the API but never drawn on the plan are not touched by it.
- `web/` (Vue 3 + TS, Leaflet CRS.Simple, ECharts): `npm --prefix web run dev` (127.0.0.1:5173, proxies /api and /sim), `npm --prefix web run build` (type check), `npm --prefix web test`. After a contract change: `npm --prefix web run api-types`. In Docker it is served by nginx on :8080. The `.claude/launch.json` entry `web-dev` starts the dev server for the browser preview.
- Plan editor: `EditorView.vue` is a 3D Sims-style build mode on the same MapLibre + three.js scene (catalogue of buildings / rooms / sensors / zones dragged out on a 1 m grid, move, resize by corners, walls-down per floor, undo/redo, saves via POST /layout); the pure model is `web/src/lib/editor.ts` (tested). Sensors can be registered in a building without a place (POST /sensors) and placed later; forecasting `api/app/predict/model.py` (pure, tested: two-window trend gated by z-score and R², step cut); metrics names in `common/scada_common/metrics.py`, Prometheus/Grafana under compose profile `observability` (`python dev.py obs`); load test `tools/loadtest.py` writes `docs/perf.md`.
- The map (`/`, `MapView.vue`) is MapLibre GL on OSM/Esri raster tiles + a three.js custom layer (`web/src/lib/realmap.ts`) with procedural models (`models3d.ts`). Every site and vehicle is drawn with its own linearised anchor (`anchorMatrix`): one transform for a 50 km region drifts off the basemap. Plan metres -> WGS84 via `georef` (`web/src/lib/geo.ts` mirrors `scada_common/geo.py`). Click: vehicle = follow + trip card; building = x-ray (rooms + sensor pins of a floor); empty space clears. Search: `lib/search.ts`. Sensors blink on every report (`live.onSensorEvent`). Tiles need internet; without them the sites still render.
- Overview screens: `/sites` (objects), `/fleet` (transport with trips), `/telemetry` (sensor cards with 3 h trends); they link into the map with `/?site=`, `/?building=`, `/?vehicle=`, `/?sensor=`. Replay and heatmap (`/analytics`) run on the same 3D region map (`web/src/lib/mapbase.ts`, `SiteLayer.setPoses`).
- The plan is a region: `kind=site` features (Podolsk plant at the origin, РЦ «Домодедово», cold store «Чехов»); `roads.road_class` = site | public, public roads come from OSRM once and are cached in `deploy/seed/routes.json` (`build_layout.py --refresh-routes`). Sites are placed so each access road docks onto a routable real road (≤ 25 m) and no highway crosses a site: `build_layout.py` warns, `test_access_roads_dock_onto_real_roads_and_highways_avoid_sites` fails otherwise. Buildings get `site_id` by containment. `deploy/seed/sensors.json` holds sensors registered in a building without a place on the plan (the lazy path; the building card recommends placing them).
- Long-running services have `restart: unless-stopped`; the worker consume loop survives any batch error, and `reclaim` (XAUTOCLAIM) takes over messages left pending by dead replicas. Worker stats are per replica: `worker:stats:<name>`.
- Python Dockerfiles install third-party deps in a layer keyed only on pyproject.toml, then our code with `--no-deps`: code edits rebuild offline. The repo-root `.dockerignore` keeps venv/web/.git out of the build context.
- Coordinates everywhere are local plan metres from `deploy/seed/layout.geojson` (generated by `build_layout.py`; edit the script, not the JSON).
- Stack Postgres listens on host port 5433 (5432 is taken by a local Postgres on this machine). Host-side commands get `.env` via dev.py.
- After changing `db/scada_db/models.py`: `python dev.py revision "msg"`, review the file, `python dev.py migrate`; `test_models_match_migrations` fails if they drift.
- After changing any schema run `python dev.py contracts` and commit `contracts/` — the frontend generates types from it.
- Commits: no Claude co-author trailer.
