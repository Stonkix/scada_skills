"""Mock auth: any of the demo users with password "demo". Real JWT + argon2 arrive in stage 4."""

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, status

from app.auth.schemas import LoginRequest, RefreshRequest, Role, TokenPair, User

router = APIRouter(prefix="/auth", tags=["auth"])

_USERS = {
    "dispatcher": User(id=1, username="dispatcher", full_name="Иванов И. И.", role=Role.DISPATCHER),
    "security": User(id=2, username="security", full_name="Петров П. П.", role=Role.SECURITY),
    "admin": User(id=3, username="admin", full_name="Смирнова А. А.", role=Role.ADMIN),
}
_MOCK_PASSWORD = "demo"


def _tokens(user: User) -> TokenPair:
    return TokenPair(access_token=f"mock-access.{user.username}", refresh_token=f"mock-refresh.{user.username}",
                     expires_in=900, user=user)


def _user_from_token(token: str, prefix: str) -> User:
    username = token.removeprefix(prefix)
    if not token.startswith(prefix) or username not in _USERS:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    return _USERS[username]


@router.post("/login", response_model=TokenPair)
def login(body: LoginRequest) -> TokenPair:
    user = _USERS.get(body.username)
    if user is None or body.password != _MOCK_PASSWORD:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")
    return _tokens(user)


@router.post("/refresh", response_model=TokenPair)
def refresh(body: RefreshRequest) -> TokenPair:
    return _tokens(_user_from_token(body.refresh_token, "mock-refresh."))


@router.get("/me", response_model=User)
def me(authorization: Annotated[str, Header()]) -> User:
    return _user_from_token(authorization.removeprefix("Bearer "), "mock-access.")
