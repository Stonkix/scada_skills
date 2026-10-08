import jwt
from fastapi import APIRouter, HTTPException, status
from scada_db import models as m
from sqlalchemy import select

from app.auth.schemas import LoginRequest, RefreshRequest, TokenPair, User
from app.auth.security import Principal, decode, hash_password, issue, needs_rehash, verify_password
from app.config import api_settings
from app.deps import DB
from app.deps import User as CurrentUser

router = APIRouter(prefix="/auth", tags=["auth"])

PERMISSIONS = {
    "map:view": "карта и live-состояние",
    "sensors:view": "реестр датчиков, история",
    "sensors:edit": "создание и правка датчиков, порогов",
    "layout:edit": "сохранение плана предприятия",
    "alerts:ack": "подтверждение и закрытие тревог",
    "kpi:view": "KPI, replay, heatmap",
    "people:view_pii": "ФИО владельцев пропусков без маскировки",
    "whitelist:edit": "правка списков допуска",
    "connectors:manage": "выпуск и отзыв API-ключей для датчиков и шлюзов",
}


def _principal(db: DB, user: m.User) -> Principal:
    role = db.get(m.RoleRow, user.role_id)
    return Principal(user.id, user.username, user.full_name, str(user.role_id), frozenset(role.permissions if role else []))


def _tokens(p: Principal) -> TokenPair:
    return TokenPair(access_token=issue(p, "access"), refresh_token=issue(p, "refresh"),
                     expires_in=api_settings.access_ttl_s,
                     user=User(id=p.id, username=p.username, full_name=p.full_name, role=p.role))


@router.post("/login", response_model=TokenPair, responses={401: {"description": "Wrong username or password"}})
def login(body: LoginRequest, db: DB) -> TokenPair:
    user = db.scalars(select(m.User).where(m.User.username == body.username, m.User.is_active)).first()
    if user is None or not verify_password(user.password_hash, body.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
        db.commit()
    return _tokens(_principal(db, user))


@router.post("/refresh", response_model=TokenPair, responses={401: {"description": "Invalid or expired refresh token"}})
def refresh(body: RefreshRequest, db: DB) -> TokenPair:
    """New token pair; role and permissions are re-read, so role changes apply on the next refresh."""
    try:
        user_id = int(decode(body.refresh_token, "refresh")["sub"])
    except jwt.InvalidTokenError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid refresh token: {e}") from e
    user = db.get(m.User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User is disabled")
    return _tokens(_principal(db, user))


@router.get("/me", response_model=User)
def me(user: CurrentUser) -> User:
    return User(id=user.id, username=user.username, full_name=user.full_name, role=user.role)


@router.get("/permissions", response_model=dict[str, str])
def permissions() -> dict[str, str]:
    """Справочник прав (роль `admin` имеет `*`)."""
    return PERMISSIONS
