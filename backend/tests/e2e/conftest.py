"""Fixture overrides for the browser smoke tests.

The e2e package talks to live HTTP servers only, so the shared database fixtures from ``tests/conftest.py``
(schema migration, per-test TRUNCATE, engine disposal) are replaced with no-ops here. This also avoids a clash
between the sync Playwright event loop and pytest-asyncio's runner for the autouse async fixtures.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def _schema() -> None:
    """No database schema needed: the servers under test own their database."""


@pytest.fixture(autouse=True)
def _truncate_tables() -> None:
    """Never truncate anything from the browser tests."""


@pytest.fixture(scope="session", autouse=True)
def _dispose_engine() -> None:
    """No engine is created by the browser tests."""
