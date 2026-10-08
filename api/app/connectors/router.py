"""Level 3 support for the UI: connection endpoints, API keys for devices/gateways, recent rejections.

The ingest itself lives in the connectors service; this module only manages what it needs.
"""

import hashlib
import json
import os
import secrets
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from scada_common import keys
from scada_db import models as m
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth.security import Principal
from app.connectors.schemas import ApiKey, ApiKeyCreate, ApiKeyIssued, Endpoints, Rejection
from app.deps import DB, redis_sync, require

router = APIRouter(prefix="/connectors", tags=["connectors"])
CanView = Annotated[Principal, Depends(require("sensors:view"))]
CanManage = Annotated[Principal, Depends(require("connectors:manage"))]
KEY_PREFIX_LEN = 12  # connectors look keys up by key[:12]


def _model(k: m.ApiKey) -> ApiKey:
    return ApiKey(id=k.id, name=k.name, key_prefix=k.key_prefix, sensor_types=k.sensor_types,
                  rate_limit_per_min=k.rate_limit_per_min, created_at=k.created_at, revoked_at=k.revoked_at)


def new_key() -> str:
    """`sk_<8 hex>_<secret>`: the first 12 characters are a unique, non-secret lookup prefix."""
    return f"sk_{secrets.token_hex(4)}_{secrets.token_urlsafe(24)}"


@router.get("/endpoints", response_model=Endpoints)
def endpoints(_: CanView) -> Endpoints:
    port = os.environ.get("CONNECTORS_PORT", "8001")
    return Endpoints(
        http_base=os.environ.get("PUBLIC_CONNECTORS_URL", f"http://localhost:{port}"),
        mqtt_host=os.environ.get("PUBLIC_MQTT_HOST", "localhost"),
        mqtt_port=int(os.environ.get("PUBLIC_MQTT_PORT", os.environ.get("MQTT_PORT", "1883"))),
    )


@router.get("/keys", response_model=list[ApiKey])
def list_keys(db: DB, _: CanManage) -> list[ApiKey]:
    return [_model(k) for k in db.scalars(select(m.ApiKey).order_by(m.ApiKey.revoked_at.is_not(None), m.ApiKey.id))]


@router.post("/keys", response_model=ApiKeyIssued, status_code=status.HTTP_201_CREATED)
def issue_key(body: ApiKeyCreate, db: DB, _: CanManage) -> ApiKeyIssued:
    """Выпустить ключ. Сам ключ возвращается **только в этом ответе**; коннекторы примут его в течение ~5 с."""
    for _attempt in range(3):  # prefix collision is astronomically rare, but the column is unique
        key = new_key()
        row = m.ApiKey(name=body.name, key_prefix=key[:KEY_PREFIX_LEN], key_hash=hashlib.sha256(key.encode()).hexdigest(),
                       sensor_types=[t.value for t in body.sensor_types] if body.sensor_types else None,
                       rate_limit_per_min=body.rate_limit_per_min)
        db.add(row)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            continue
        return ApiKeyIssued(**_model(row).model_dump(), key=key)
    raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Could not allocate a unique key prefix")


@router.delete("/keys/{key_id}", response_model=ApiKey)
def revoke_key(key_id: int, db: DB, _: CanManage) -> ApiKey:
    """Отозвать ключ. Коннекторы перестают его принимать в течение 30 с (интервал обновления их кэша)."""
    row = db.get(m.ApiKey, key_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"API key {key_id} not found")
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        db.commit()
    return _model(row)


@router.get("/rejections", response_model=list[Rejection])
def rejections(
    _: CanView,
    limit: Annotated[int, Query(ge=1, le=200)] = 30,
    adapter: str | None = None,
) -> list[Rejection]:
    """Последние отклонённые сообщения (stream:dlq), новые сверху: что пришло и почему не принято."""
    out: list[Rejection] = []
    for entry_id, f in redis_sync().xrevrange(keys.STREAM_DLQ, count=limit if adapter is None else limit * 10):
        if adapter and f.get("adapter") != adapter:
            continue
        try:
            detail = json.loads(f["detail"]) if f.get("detail") else None
        except ValueError:
            detail = f.get("detail")
        out.append(Rejection(id=entry_id, received_at=f.get("received_at"), reason=f.get("reason", "?"),
                             adapter=f.get("adapter"), source=f.get("source"), api_key=f.get("api_key"),
                             detail=detail, raw=f.get("raw")))
        if len(out) >= limit:
            break
    return out
