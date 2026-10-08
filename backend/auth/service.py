"""
auth/service.py
---------------
Authentication business logic: user lookup, password verification,
JWT guard dependencies for role-based access control.
"""

import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.dependencies import get_db_session
from core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from core import token_store
from db.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")
# Logout accepts a request without a bearer token (it is idempotent), so its
# token dependency must not 401 on its own.
optional_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)

# Precomputed once at import: a valid bcrypt hash used only to spend the same
# hashing time on the "no such user" path as on a real verify, so response
# timing does not reveal whether an email is registered (F31).
_DUMMY_PASSWORD_HASH = hash_password("timing-equalizer-not-a-real-password")


async def authenticate_user(
    email: str,
    password: str,
    db: AsyncSession,
) -> User | None:
    """
    Look up user by email and verify bcrypt password.
    Returns the User ORM instance on success, None on failure.

    Runs a bcrypt verify on every path — including when the user does not exist
    — so login timing cannot be used to enumerate registered emails (F31).
    """
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None:
        verify_password(password, _DUMMY_PASSWORD_HASH)  # equalize timing
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user


async def register_user(
    email: str,
    password: str,
    db: AsyncSession,
) -> User:
    """
    Create a new student user account.
    Raises ValueError if email already exists.
    """
    result = await db.execute(select(User).where(User.email == email))
    if result.scalar_one_or_none():
        raise ValueError("Email already exists.")

    user = User(
        email=email,
        hashed_password=hash_password(password),
        role="student",
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db_session),
) -> User:
    """
    Decode JWT and load the corresponding User from the database.
    Raises HTTP 401 on invalid/expired token or missing user.

    Only access tokens are accepted here — a refresh token presented as a bearer
    is rejected (F19) — and a token revoked by logout is rejected too.
    """
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(token)
    except JWTError:
        raise credentials_exc

    if payload.type != "access":
        raise credentials_exc
    # Tokens issued before access tokens carried a jti cannot be revoked; they
    # simply expire (jwt_expire_minutes).
    if payload.jti and await token_store.is_revoked(db, payload.jti):
        raise credentials_exc

    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exc
    return user


# ── Session tokens (F19) ──────────────────────────────────────────────────────

def issue_token_pair(user: User) -> tuple[str, str]:
    """Return a fresh (access_token, refresh_token) pair for a user."""
    data = {"sub": str(user.id), "email": user.email, "role": user.role}
    # Both tokens carry a jti so either can be revoked (logout, rotation).
    access = create_access_token({**data, "jti": uuid.uuid4().hex})
    refresh = create_refresh_token(data, jti=uuid.uuid4().hex)
    return access, refresh


async def refresh_token_pair(
    refresh_token: str,
    db: AsyncSession,
) -> tuple[str, str, str]:
    """
    Validate a refresh token and rotate it: revoke the old jti and return a new
    (access, refresh, role). Raises ValueError (→ 401 at the router) on any
    invalid/expired/revoked/mistyped token or missing user.
    """
    try:
        payload = decode_token(refresh_token)
    except JWTError:
        raise ValueError("Invalid or expired refresh token.")

    if payload.type != "refresh" or not payload.jti:
        raise ValueError("Not a refresh token.")
    if await token_store.is_revoked(db, payload.jti):
        raise ValueError("Refresh token has been revoked.")

    result = await db.execute(select(User).where(User.email == payload.email))
    user = result.scalar_one_or_none()
    if user is None:
        raise ValueError("User no longer exists.")

    # Rotate: the presented refresh token is single-use.
    await token_store.revoke(db, payload.jti, payload.exp)
    access, refresh = issue_token_pair(user)
    return access, refresh, user.role


async def revoke_session(
    db: AsyncSession,
    access_token: str | None,
    refresh_token: str | None = None,
) -> int:
    """
    Logout: revoke the caller's access token and, if given, their refresh token.

    Best-effort and silent on bad input -- logout is idempotent and must not
    reveal anything about a token. Returns how many tokens were revoked.
    """
    revoked = 0
    for token, expected_type in ((access_token, "access"), (refresh_token, "refresh")):
        if not token:
            continue
        try:
            payload = decode_token(token)
        except JWTError:
            continue
        if payload.type == expected_type and payload.jti:
            await token_store.revoke(db, payload.jti, payload.exp)
            revoked += 1
    return revoked


async def require_teacher(
    current_user: User = Depends(get_current_user),
) -> User:
    """Raise HTTP 403 if the authenticated user is not a teacher."""
    if current_user.role != "teacher":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Teacher role required.",
        )
    return current_user


async def require_student(
    current_user: User = Depends(get_current_user),
) -> User:
    """Raise HTTP 403 if the authenticated user is not a student."""
    if current_user.role != "student":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Student role required.",
        )
    return current_user
