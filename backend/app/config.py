"""Centralized settings loaded from environment / .env.

Secrets are read here and nowhere else, so they never get logged by accident.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM
    llm_provider: Literal["groq", "anthropic"] = "groq"
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    # Database
    database_url: str = "postgresql+psycopg://localhost:5432/brain_os"

    # GitHub
    github_token: str = ""
    github_repo: str = ""
    github_base_branch: str = "main"

    # Pre-PR validation gate. Before opening a PR we materialize the proposed
    # files over a fresh checkout of the base branch and check them:
    #   off  - syntax check only (the historical behavior)
    #   lint - also run `ruff` (static; does NOT execute the generated code)
    #   full - also install deps + run `pytest` (DOES execute the code; only
    #          turn on where running agent-generated code on the host is
    #          acceptable, since pytest collection runs module-level code)
    # Default "lint": catch real lint/import errors without executing untrusted
    # code on the app host. CI (iterate-on-red) is the sandbox for full tests.
    prepr_validation: Literal["off", "lint", "full"] = "lint"
    prepr_validation_timeout: int = 120  # seconds, per subprocess step

    # Slack (Weekend 2)
    slack_bot_token: str = ""
    slack_watch_channel: str = ""  # channel name (e.g. #all-new-workspace) or ID


@lru_cache
def get_settings() -> Settings:
    return Settings()
