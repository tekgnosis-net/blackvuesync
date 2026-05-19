"""tests for the APScheduler integration in blackvuesync.server.scheduler."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from blackvuesync.server.progress import ProgressPublisher
from blackvuesync.server.scheduler import init_scheduler
from blackvuesync.settings import SettingsStore


@pytest.fixture()
def settings_path(tmp_path: Path) -> Path:
    """returns a settings file path inside tmp_path."""
    return tmp_path / "settings.json"


def _make_store(settings_path: Path) -> SettingsStore:
    """creates a SettingsStore with a dummy address."""
    with patch.dict(os.environ, {"ADDRESS": "192.168.0.1"}, clear=False):
        return SettingsStore(settings_path)


class TestInitScheduler:
    """tests for init_scheduler()."""

    def test_returns_running_scheduler_with_one_job(self, settings_path: Path) -> None:
        store = _make_store(settings_path)
        publisher = ProgressPublisher()
        scheduler = init_scheduler(store, publisher)
        try:
            assert scheduler.running is True
            jobs = scheduler.get_jobs()
            assert len(jobs) == 1
            assert jobs[0].id == "sync"
        finally:
            scheduler.shutdown(wait=False)
