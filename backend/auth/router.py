"""
auth/router.py
--------------
FastAPI routes for authentication.

Routes:
  POST /auth/login  — validate credentials, return JWT
  POST /auth/logout — revoke the caller's access token (+ refresh token if sent)
  GET  /auth/me     — return current user identity from JWT
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from auth.schemas import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from auth.service import (
    authenticate_user,
    get_current_user,
    issue_token_pair,
    optional_oauth2_scheme,
    refresh_token_pair,
    register_user,
    revoke_session,
)
from core.dependencies import get_db_session
from db.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse, summary="Login and receive JWT")
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db_session),
) -> TokenResponse:
    user = await authenticate_user(body.email, body.password, db)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access, refresh = issue_token_pair(user)
    return TokenResponse(access_token=access, refresh_token=refresh, role=user.role)


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED, summary="Register a new student account")
async def register(
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db_session),
) -> TokenResponse:
    try:
        user = await register_user(body.email, body.password, db)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )
    access, refresh = issue_token_pair(user)
    return TokenResponse(access_token=access, refresh_token=refresh, role=user.role)


@router.post("/refresh", response_model=TokenResponse, summary="Exchange a refresh token for a new token pair")
async def refresh(
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db_session),
) -> TokenResponse:
    """Rotate the refresh token: the presented one is revoked, a fresh pair returned."""
    try:
        access, new_refresh, role = await refresh_token_pair(body.refresh_token, db)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )
    return TokenResponse(access_token=access, refresh_token=new_refresh, role=role)


@router.post("/logout", summary="End the session: revoke the access token (and refresh token)")
async def logout(
    body: LogoutRequest | None = None,
    access_token: str | None = Depends(optional_oauth2_scheme),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """
    Revoke the bearer access token, so it is refused from now on even though it
    has not expired, and the refresh token if one is sent. Persisted, so it
    survives a restart. Idempotent: always 200, even with nothing to revoke.
    """
    await revoke_session(db, access_token, body.refresh_token if body else None)
    return {"status": "logged_out"}


@router.get("/me", response_model=UserResponse, summary="Return current user identity")
async def me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse(id=current_user.id, email=current_user.email, role=current_user.role)
