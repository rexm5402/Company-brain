"""pytest fixtures shared across the test suite.

Uses a separate `brain_os_test` database so tests never touch the dev DB. The
test DB is created if absent, all migrations are applied at the start of the
session, and each test runs inside a transaction that is rolled back so no data
leaks between tests.
"""
from __future__ import annotations

import os

# Point at the test DB BEFORE any app module is imported (settings are cached).
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://localhost:5432/brain_os_test"
)
# Disable AI calls so unit tests never hit a real LLM.
os.environ.setdefault("GROQ_API_KEY", "test-key-unused")
os.environ.setdefault("GITHUB_TOKEN", "test-token-unused")
os.environ.setdefault("GITHUB_REPO", "test/repo")
# Skip pre-PR validation in tests by default (tested separately).
os.environ.setdefault("PREPR_VALIDATION", "off")

import pytest
import sqlalchemy as sa
from sqlalchemy import text

from app.config import get_settings
from app.db import Base, SessionLocal, engine


def _ensure_test_db() -> None:
    """Create the test database if it doesn't exist yet."""
    url = get_settings().database_url
    # Connect to the default `postgres` database to issue CREATE DATABASE.
    admin_url = url.rsplit("/", 1)[0] + "/postgres"
    db_name = url.rsplit("/", 1)[-1].split("?")[0]
    try:
        admin_engine = sa.create_engine(
            admin_url, isolation_level="AUTOCOMMIT", pool_pre_ping=True
        )
        with admin_engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"),
                {"n": db_name},
            ).scalar()
            if not exists:
                conn.execute(text(f"CREATE DATABASE {db_name}"))
        admin_engine.dispose()
    except Exception:  # noqa: BLE001 - if we can't create it, the tests will fail naturally
        pass


def pytest_sessionstart(session: pytest.Session) -> None:
    _ensure_test_db()
    # Import all models so they register on Base.metadata.
    import app.audit.models  # noqa: F401
    import app.runs.models  # noqa: F401
    import app.tickets.models  # noqa: F401
    import app.reports.models  # noqa: F401
    import app.chat.models  # noqa: F401
    import app.users.models  # noqa: F401
    import app.watchdog.models  # noqa: F401
    import app.notifications.models  # noqa: F401
    import app.repos.models  # noqa: F401
    import app.deployments.models  # noqa: F401
    import app.memory.models  # noqa: F401

    Base.metadata.create_all(engine)


@pytest.fixture(autouse=True)
def rollback_after_test():
    """Wrap every test in a transaction, roll back afterwards.

    This means tests can write rows freely and nothing leaks between them.
    Each test gets a clean slate without dropping/recreating tables.
    """
    connection = engine.connect()
    transaction = connection.begin()

    # Patch SessionLocal so it uses OUR connection (and therefore our txn).
    import app.db as db_module

    original_factory = db_module.SessionLocal

    from sqlalchemy.orm import sessionmaker

    TestSession = sessionmaker(bind=connection)
    db_module.SessionLocal = TestSession
    # Also patch the module-level `SessionLocal` that other modules may have
    # already imported as a name.
    import app.tickets.service as ts
    import app.reports.service as rs
    import app.chat.service as cs
    import app.repos.service as repos_svc
    import app.deployments.service as deploy_svc
    import app.notifications.service as notif_svc
    import app.watchdog.service as watchdog_svc

    ts.SessionLocal = TestSession
    rs.SessionLocal = TestSession
    cs.SessionLocal = TestSession
    repos_svc.SessionLocal = TestSession
    deploy_svc.SessionLocal = TestSession
    notif_svc.SessionLocal = TestSession
    watchdog_svc.SessionLocal = TestSession

    yield

    db_module.SessionLocal = original_factory
    ts.SessionLocal = original_factory
    rs.SessionLocal = original_factory
    cs.SessionLocal = original_factory
    repos_svc.SessionLocal = original_factory
    deploy_svc.SessionLocal = original_factory
    notif_svc.SessionLocal = original_factory
    watchdog_svc.SessionLocal = original_factory

    transaction.rollback()
    connection.close()
