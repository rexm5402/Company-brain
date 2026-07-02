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

    # Token optimisation via Headroom (https://headroomlabs-ai.github.io/headroom/)
    # Compresses tool outputs + conversation history before each LLM call.
    # Set to false only if you need to debug raw token counts.
    headroom_enabled: bool = True

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

    # Sentry APM — optional; enables the get_recent_errors agent tool
    # Create an internal integration token at sentry.io → Settings → Integrations
    sentry_auth_token: str = ""
    sentry_org: str = ""      # organisation slug, e.g. "acme-corp"
    sentry_project: str = ""  # project slug, e.g. "backend"

    # Webhook secrets — used to verify inbound payloads are genuine
    # Sentry: Settings → Developer Settings → Internal Integration → Client Secret
    sentry_webhook_secret: str = ""
    # GitHub: set when registering the webhook on your repo/org (any string you choose)
    github_webhook_secret: str = ""

    # Watchdog defaults
    # GitHub username to assign watchdog tickets when CODEOWNERS has no match
    watchdog_default_assignee: str = ""
    # GitHub username reported as the "reporter" on all watchdog-created tickets
    watchdog_reporter: str = "watchdog"
    # Slack channel to post incident notifications to (e.g. "#incidents")
    watchdog_slack_channel: str = ""

    # Ephemeral staging environments — webhook to trigger a deploy on PR open
    deploy_webhook_url: str = ""

    # OpenAI API key for pgvector embeddings (Feature 3: repo memory)
    openai_api_key: str = ""

    # GitHub OAuth + JWT session
    github_client_id: str = ""
    github_client_secret: str = ""
    jwt_secret: str = "change-me-in-production"  # override via env
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 168  # 7 days
    frontend_url: str = "http://localhost:3000"


@lru_cache
def get_settings() -> Settings:
    return Settings()
