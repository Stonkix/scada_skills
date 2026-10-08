from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.alerts.router import router as alerts_router
from app.auth.router import router as auth_router
from app.layout.router import router as layout_router
from app.live.router import router as live_router
from app.registry.router import router as registry_router

app = FastAPI(
    title="SCADA Logistics API",
    version="0.1.0",
    description=(
        "Мониторинг логистической инфраструктуры предприятия.\n\n"
        "Координаты — метры локального плана (см. `GET /objects`). Время — ISO 8601 с таймзоной.\n\n"
        "**Режим мока:** данные генерируются в памяти (`app/mock.py`), контракт совпадает с боевым. "
        "Вход: `dispatcher` / `security` / `admin`, пароль `demo`.\n\n"
        "WebSocket `/ws/live`: формат сообщений — `contracts/ws.schema.json`."
    ),
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

for r in (auth_router, layout_router, registry_router, live_router, alerts_router):
    app.include_router(r)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "mode": "mock"}
