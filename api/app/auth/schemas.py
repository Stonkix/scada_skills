from pydantic import BaseModel, Field
from scada_common.enums import Role


class User(BaseModel):
    id: int
    username: str = Field(..., examples=["dispatcher"])
    full_name: str = Field(..., examples=["Иванов И. И."])
    role: Role


class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="Время жизни access-токена, секунд")
    user: User
