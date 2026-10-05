"""Shared pytest fixtures.

Every test gets an isolated on-disk SQLite file (on-disk, not :memory:, because
the concurrency test in PRD section 6 case 2 needs multiple real connections to
race on the same UNIQUE index) and controls server time through BILLING_NOW.
"""
import os
import tempfile
from pathlib import Path

import pytest

# Keep the AI explainer off by default so the whole suite exercises the
# deterministic fallback path (GAPS.md Improvement 2).
os.environ.setdefault("AI_PROVIDER_API_KEY", "")


@pytest.fixture
def db_path():
    d = tempfile.mkdtemp(prefix="billing_test_")
    p = Path(d) / "billing.db"
    yield str(p)


@pytest.fixture
def client(db_path, monkeypatch):
    """A FastAPI TestClient wired to a fresh database."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("BILLING_PERIOD_MINUTES", "0")
    monkeypatch.setenv("AI_PROVIDER_API_KEY", "")

    from fastapi.testclient import TestClient

    from app.db.database import init_db, reset_connection_cache
    from app.main import app

    reset_connection_cache()
    init_db()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def at_time(monkeypatch):
    """Move injected server time (ARCHITECTURE.md section 7: BILLING_NOW)."""

    def _set(iso: str):
        monkeypatch.setenv("BILLING_NOW", iso)

    return _set
