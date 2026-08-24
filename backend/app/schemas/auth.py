from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.security import Role
from app.schemas.common import ORMModel


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=200)
    full_name: str = Field(default="", max_length=200)

    @field_validator("password")
    @classmethod
    def _strength(cls, v: str) -> str:
        checks = (
            any(c.isupper() for c in v),
            any(c.islower() for c in v),
            any(c.isdigit() for c in v),
            any(not c.isalnum() for c in v),
        )
        if sum(checks) < 3:
            raise ValueError(
                "Password needs at least three of: uppercase, lowercase, digit, symbol"
            )
        return v


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class LogoutIn(BaseModel):
    """Logout is account-wide; any token supplied here is accepted and ignored."""

    refresh_token: str | None = None


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 - the OAuth scheme name, not a secret
    expires_in: int


class UserOut(ORMModel):
    id: uuid.UUID
    email: EmailStr
    full_name: str
    role: Role
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime
