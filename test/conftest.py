"""shared pytest fixtures for blackvuesync tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def settings_path(tmp_path: Path) -> Path:
    """returns a fresh settings.json path inside tmp_path."""
    return tmp_path / "settings.json"
