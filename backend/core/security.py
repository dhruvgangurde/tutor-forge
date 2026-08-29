"""
core/security.py
----------------
JWT creation/decoding and bcrypt password utilities.
No route logic here — pure cryptographic helpers.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

from core.config import settings

# ── Password hashing ──────────────────────────────────────────────────────────
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain_password: str) -> str:
    """Return a bcrypt hash of the plain-text password."""
    return _pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Return True if plain_password matches hashed_password."""
    return _pwd_context.verify(plain_password, hashed_password)


# ── JWT ───────────────────────────────────────────────────────────────────────

class TokenPayload(BaseModel):
    sub: str              # user_id (UUID as string)
    email: str
    role: str
    exp: datetime
    type: str = "access"  # "access" | "refresh" (F19); defaults keep old tokens valid
    jti: str | None = None  # present on refresh tokens, used for revocation


def create_access_token(
    data: dict[str, Any],
    expires_delta: timedelta | None = None,
) -> str:
    """
    Create a signed JWT access token.

    `data` must include keys: sub (user_id), email, role.
    Expiry defaults to settings.jwt_expire_minutes if not supplied.
    """
    to_encode = data.copy()
    to_encode.setdefault("type", "access")
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.jwt_expire_minutes)
    )
    to_encode["exp"] = expire
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(
    data: dict[str, Any],
    jti: str,
    expires_delta: timedelta | None = None,
) -> str:
    """
    Create a signed refresh token (F19): longer-lived, carries type="refresh"
    and a unique jti so it can be revoked/rotated server-side.
    """
    to_encode = data.copy()
    to_encode.update({"type": "refresh", "jti": jti})
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.jwt_refresh_expire_minutes)
    )
    to_encode["exp"] = expire
    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> TokenPayload:
    """
    Validate and decode a JWT.

    Raises jose.JWTError on invalid signature or expiry.
    Callers (get_current_user) catch this and raise HTTPException(401).
    """
    payload = jwt.decode(
        token,
        settings.jwt_secret_key,
        algorithms=[settings.jwt_algorithm],
    )
    return TokenPayload(**payload)
