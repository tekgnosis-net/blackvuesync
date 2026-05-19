"""APScheduler integration: cron-triggered sync inside the long-running web service."""

from __future__ import annotations

import logging

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from blackvuesync.server.progress import ProgressPublisher
from blackvuesync.server.sync_runner import trigger_sync
from blackvuesync.settings import Settings, SettingsStore

logger = logging.getLogger(__name__)

# the single job id; reused by reschedule_job and remove_job.
_JOB_ID = "sync"


def _build_trigger(settings: Settings) -> CronTrigger:
    """builds a CronTrigger from the schedule section of settings."""
    return CronTrigger.from_crontab(
        settings.schedule.cron_expression,
        timezone=settings.schedule.timezone,
    )


def _scheduled_run(store: SettingsStore, publisher: ProgressPublisher) -> None:
    """job function: triggers a sync via the shared trigger_sync entrypoint.

    settings are read fresh on each tick so updates to e.g. address, timeout,
    or schedule.paused apply on the next scheduled run without a restart.
    """
    settings = store.get()
    if settings.schedule.paused:
        logger.info("scheduled sync skipped: schedule is paused")
        return
    result = trigger_sync(settings, publisher)
    if result["status"] == "already_running":
        logger.info(
            "scheduled sync skipped: another sync is already running (job_id=%s)",
            result["job_id"],
        )


def init_scheduler(
    store: SettingsStore, publisher: ProgressPublisher
) -> BackgroundScheduler:
    """initializes and starts a BackgroundScheduler with one cron-triggered job.

    the scheduler uses a single-thread executor so concurrent fires (e.g. on
    schedule transitions) cannot overlap. `max_instances=1` and
    `coalesce=True` further enforce that a backlog of missed runs collapses
    to one. the cron expression and timezone are read from settings at init,
    and a SettingsStore on_change listener reschedules the job in-place when
    those fields change.
    """
    scheduler = BackgroundScheduler(
        executors={"default": ThreadPoolExecutor(max_workers=1)},
        timezone=store.get().schedule.timezone,
    )
    scheduler.add_job(
        _scheduled_run,
        trigger=_build_trigger(store.get()),
        id=_JOB_ID,
        args=(store, publisher),
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    def _on_change(old: Settings, new: Settings) -> None:
        """reschedules the sync job when schedule settings change."""
        if old.schedule == new.schedule:
            return
        logger.info(
            "rescheduling sync job: %r/%s -> %r/%s",
            old.schedule.cron_expression,
            old.schedule.timezone,
            new.schedule.cron_expression,
            new.schedule.timezone,
        )
        scheduler.reschedule_job(_JOB_ID, trigger=_build_trigger(new))

    store.on_change(_on_change)
    scheduler.start()
    return scheduler


__all__ = ["init_scheduler"]
