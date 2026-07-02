"""GitHub OAuth + JWT service."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
import jwt

from app.config import get_settings

GITHUB_AUTH_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API_URL = "https://api.github.com"


def github_authorize_url(state: str) -> str:
    """Return the GitHub OAuth redirect URL."""
    settings = get_settings()
    params = urlencode(
        {
            "client_id": settings.github_client_id,
            "scope": "read:user user:email",
            "state": state,
        }
    )
    return f"{GITHUB_AUTH_URL}?{params}"


def exchange_code(code: str) -> dict:
    """Exchange OAuth code for GitHub access token. Returns token dict."""
    settings = get_settings()
    resp = httpx.post(
        GITHUB_TOKEN_URL,
        json={
            "client_id": settings.github_client_id,
            "client_secret": settings.github_client_secret,
            "code": code,
        },
        headers={"Accept": "application/json"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise ValueError(data.get("error_description", data["error"]))
    return data


def get_github_user(access_token: str) -> dict:
    """Fetch GitHub user profile using the access token."""
    resp = httpx.get(
        f"{GITHUB_API_URL}/user",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github+json",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def create_jwt(github_username: str, display_name: str, avatar_url: str) -> str:
    """Create a signed JWT for the session."""
    settings = get_settings()
    payload = {
        "sub": github_username,
        "name": display_name,
        "avatar": avatar_url,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expire_hours),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_jwt(token: str) -> dict:
    """Decode and verify a JWT. Raises jwt.InvalidTokenError on failure."""
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
