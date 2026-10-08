import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ApiSettings:
    jwt_secret: str = field(default_factory=lambda: os.environ.get("JWT_SECRET", "dev-only-jwt-secret-change-me-32bytes-min"))
    jwt_algorithm: str = "HS256"
    access_ttl_s: int = field(default_factory=lambda: int(os.environ.get("JWT_ACCESS_TTL_S", "900")))
    refresh_ttl_s: int = field(default_factory=lambda: int(os.environ.get("JWT_REFRESH_TTL_S", str(7 * 24 * 3600))))
    cors_origins: list[str] = field(default_factory=lambda: os.environ.get("CORS_ORIGINS", "*").split(","))


api_settings = ApiSettings()
