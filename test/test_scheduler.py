"""tests for the APScheduler integration in blackvuesync.server.scheduler."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from blackvuesync.server.progress import ProgressPublisher
from blackvuesync.server.scheduler import _JOB_ID, init_scheduler
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

    def test_on_change_reschedules_when_cron_changes(self, settings_path: Path) -> None:
        """changing schedule.cron_expression rebuilds the job trigger."""
        store = _make_store(settings_path)
        publisher = ProgressPublisher()
        scheduler = init_scheduler(store, publisher)
        try:
            original_next = scheduler.get_job(_JOB_ID).trigger
            # changes cron expression from every-15-min default to every-5-min
            store.update(
                lambda s: dataclasses.replace(
                    s,
                    schedule=dataclasses.replace(
                        s.schedule, cron_expression="*/5 * * * *"
                    ),
                )
            )
            new_trigger = scheduler.get_job(_JOB_ID).trigger
            assert str(new_trigger) != str(original_next)
            assert "minute='*/5'" in str(new_trigger)
        finally:
            scheduler.shutdown(wait=False)

    def test_on_change_noop_when_schedule_unchanged(self, settings_path: Path) -> None:
        """changing a non-schedule field does not reschedule the job."""
        store = _make_store(settings_path)
        publisher = ProgressPublisher()
        scheduler = init_scheduler(store, publisher)
        try:
            original = scheduler.get_job(_JOB_ID).trigger
            # changes a non-schedule field
            store.update(
                lambda s: dataclasses.replace(
                    s,
                    sync=dataclasses.replace(s.sync, grouping="daily"),
                )
            )
            assert str(scheduler.get_job(_JOB_ID).trigger) == str(original)
        finally:
            scheduler.shutdown(wait=False)
