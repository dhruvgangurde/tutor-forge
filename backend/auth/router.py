"""
auth/router.py
--------------
FastAPI routes for authentication.

Routes:
  POST /auth/login  — validate credentials, return JWT
  GET  /auth/me     — return current user identity from JWT
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from auth.schemas import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from auth.service import (
    authenticate_user,
    get_current_user,
    issue_token_pair,
    refresh_token_pair,
    register_user,
    revoke_refresh_token,
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


@router.post("/logout", summary="Revoke a refresh token")
async def logout(body: RefreshRequest) -> dict:
    """Revoke the presented refresh token so it can no longer be rotated."""
    revoke_refresh_token(body.refresh_token)
    return {"status": "logged_out"}


@router.get("/me", response_model=UserResponse, summary="Return current user identity")
async def me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse(id=current_user.id, email=current_user.email, role=current_user.role)
