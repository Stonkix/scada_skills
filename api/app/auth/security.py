"""Passwords (argon2id) and tokens (JWT HS256: short-lived access, long-lived refresh)."""

import time
from dataclasses import dataclass
from typing import Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.config import api_settings

_hasher = PasswordHasher()
TokenType = Literal["access", "refresh"]


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


@dataclass(frozen=True)
class Principal:
    """Who is calling. Permissions travel in the access token so requests need no DB lookup."""

    id: int
    username: str
    full_name: str
    role: str
    permissions: frozenset[str]

    def can(self, permission: str) -> bool:
        return "*" in self.permissions or permission in self.permissions


def issue(p: Principal, kind: TokenType) -> str:
    now = int(time.time())
    ttl = api_settings.access_ttl_s if kind == "access" else api_settings.refresh_ttl_s
    claims = {"sub": str(p.id), "typ": kind, "iat": now, "exp": now + ttl}
    if kind == "access":
        claims |= {"usr": p.username, "name": p.full_name, "role": p.role, "perm": sorted(p.permissions)}
    return jwt.encode(claims, api_settings.jwt_secret, algorithm=api_settings.jwt_algorithm)


def decode(token: str, kind: TokenType) -> dict:
    """Raises jwt.InvalidTokenError on a bad signature, expiry or wrong token type."""
    claims = jwt.decode(token, api_settings.jwt_secret, algorithms=[api_settings.jwt_algorithm],
                        options={"require": ["sub", "typ", "exp"]})
    if claims["typ"] != kind:
        raise jwt.InvalidTokenError(f"expected a {kind} token")
    return claims


def principal_from_access(token: str) -> Principal:
    c = decode(token, "access")
    return Principal(int(c["sub"]), c["usr"], c["name"], c["role"], frozenset(c["perm"]))
