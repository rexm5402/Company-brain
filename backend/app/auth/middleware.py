"""FastAPI dependency for extracting the current user from a JWT cookie."""
from __future__ import annotations

import jwt as pyjwt
from fastapi import HTTPException, Request

from app.auth.service import decode_jwt


def get_current_user(request: Request) -> dict:
    """FastAPI dependency — extracts and verifies JWT from cookie or Authorization header."""
    token = request.cookies.get("brain_os_token")
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        return decode_jwt(token)
    except pyjwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def get_optional_user(request: Request) -> dict | None:
    """Like get_current_user but returns None instead of raising."""
    try:
        return get_current_user(request)
    except HTTPException:
        return None
