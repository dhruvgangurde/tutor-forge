"""
auth/schemas.py
---------------
Pydantic v2 request/response schemas for the auth package.
"""

import uuid
from typing import Annotated

from pydantic import BaseModel, EmailStr, Field, field_validator

from core.limits import MAX_EMAIL_CHARS

Email = Annotated[EmailStr, Field(max_length=MAX_EMAIL_CHARS)]

# bcrypt hashes at most the first 72 bytes of a password; anything longer is
# silently truncated, so two different long passwords sharing a 72-byte prefix
# would authenticate identically. Reject those at registration (F17).
_BCRYPT_MAX_BYTES = 72


class LoginRequest(BaseModel):
    email: Email
    password: str


class RegisterRequest(BaseModel):
    email: Email
    password: Annotated[str, Field(min_length=8)]

    @field_validator("password")
    @classmethod
    def password_within_bcrypt_limit(cls, v: str) -> str:
        if len(v.encode("utf-8")) > _BCRYPT_MAX_BYTES:
            raise ValueError(
                f"password must be at most {_BCRYPT_MAX_BYTES} bytes long."
            )
        return v


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    refresh_token: str | None = None  # F19: additive; omitted for legacy callers


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    """Optional body for /auth/logout; the bearer access token is always revoked."""
    refresh_token: str | None = None


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    role: str
