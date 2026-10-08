"""Shared dependencies: storage handles and the caller's identity / permissions.

Postgres and ClickHouse calls are synchronous and run in FastAPI's threadpool
(plain `def` endpoints); Redis is async for live state and the WebSocket.
"""

from collections.abc import Iterator
from functools import lru_cache
from typing import Annotated

import clickhouse_connect
import jwt
import redis
import redis.asyncio as aioredis
from clickhouse_connect.driver.client import Client as ClickHouse
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from scada_db.config import settings
from scada_db.postgres import engine
from sqlalchemy.orm import Session

from app.auth.security import Principal, principal_from_access

# one ClickHouse client is shared by threadpool workers: without a session id queries may run concurrently
clickhouse_connect.common.set_setting("autogenerate_session_id", False)


@lru_cache
def clickhouse() -> ClickHouse:
    return clickhouse_connect.get_client(host=settings.ch_host, port=settings.ch_port, username=settings.ch_user,
                                         password=settings.ch_password, database=settings.ch_db)


@lru_cache
def redis_sync() -> redis.Redis:
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


def db() -> Iterator[Session]:
    with Session(engine()) as s:
        yield s


def redis_async(request: Request) -> aioredis.Redis:
    return request.app.state.redis


_bearer = HTTPBearer(auto_error=False)


def current_user(creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]) -> Principal:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    try:
        return principal_from_access(creds.credentials)
    except jwt.InvalidTokenError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid token: {e}",
                            headers={"WWW-Authenticate": "Bearer"}) from e


def require(permission: str):
    def check(user: Annotated[Principal, Depends(current_user)]) -> Principal:
        if not user.can(permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Role '{user.role}' lacks permission '{permission}'")
        return user

    return check


DB = Annotated[Session, Depends(db)]
CH = Annotated[ClickHouse, Depends(clickhouse)]
User = Annotated[Principal, Depends(current_user)]
