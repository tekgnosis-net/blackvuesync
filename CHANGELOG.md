# CHANGELOG

Versions 2.3.0 onward are from the tekgnosis-net fork. Pre-release versions
(`aN`/`bN`) track the sub-project that introduced them.

## Unreleased

Fixes from a full code review. See [docs/guide/upgrading.md](docs/guide/upgrading.md#upgrading-to-the-release-after-280a0) for what to check after upgrading.

* The web service now applies `retention.keep`, `sync.retry_failed_after`, `sync.skip_metadata` and `sync.affinity_key`; previously scheduled syncs ignored them, so old recordings were never deleted. An empty `keep` keeps recordings forever.
* Live progress and log streaming work under waitress; they returned 500 in production.
* A download cut short by the dashcam is kept as a partial and resumed, instead of being saved as complete.
* Fix a file-descriptor leak on every scheduled sync that exhausted the process after about 10 days.
* Security: `PATCH /api/settings/auth` can no longer overwrite the password hash or session secret; `X-Forwarded-For` is only honored with `BLACKVUESYNC_TRUST_PROXY`, so proxy auth and the login rate limiter can't be spoofed; no public fallback session key; password changes and session rotation sign out other sessions immediately; CSP drops `'unsafe-inline'`.
* Cron schedules follow standard cron: day-of-week `0`/`7` is Sunday (previously Monday), and day-of-month/day-of-week combine with OR. Invalid cron expressions and timezones are rejected, and a bad stored schedule falls back to `*/15 * * * *` UTC instead of crash-looping.
* Settings are type-checked (422 instead of 500); `keep`, `retry_failed_after` and include/exclude codes use the same parsers as the CLI.
* `BLACKVUESYNC_ADMIN_PASSWORD` now sets the admin password on first start.
* Progress counts files rather than recordings, reports already-downloaded files as skipped, and shows early failures such as an unreachable dashcam.
* Viewer: recordings with `L`/`S` upload-flag filenames play; rear-only recordings play once; GPS timing no longer runs ahead of the video; `nan` speeds no longer produce invalid JSON.
* The web UI shows errors for failed actions, redirects to login when a session expires, and keeps working on pages open longer than an hour.
* Docker: `/config` is created and owned by the service user, so the container starts without a `/config` mount; waitress runs 32 threads; at most 16 live-update streams.

## 2.8.0a0

* Add recording viewer (`/viewer`): front/rear playback, GPS track on a map, G-sensor chart, and journey auto-advance. New `viewer` settings section (`journey_mode`, `speed_unit`). (#21)

## 2.7.0a0

* Add statistics page (`/stats`): per-run history in `/config/stats.db` and a disk-usage forecast. New `stats` settings section (`retention_days`). (#20)

## 2.6.0a0

* Add live log viewer (`/logs`) backed by an in-memory ring buffer and a rotating log file under `/config/logs/`. (#19)

## 2.5.0a0

* Add settings UI (`/settings`) covering all settings sections, password change, and session rotation. (#18)

## 2.4.0b0

* Add dashboard with live progress, Sync now, Stop, Pause/Resume, and storage, dashcam, next-run and recent-activity cards. (#11, #12, #17)
* Add read-only dashcam config info card. (#12)
* Resume interrupted downloads with HTTP range requests. (#13)
* Multi-stage Docker image; `uv` is no longer in the final image. (#14)
* Apply logging setting changes without a restart. (#15)

## 2.3.0

* Restructure as a package with `sync` and `serve` subcommands; `blackvuesync <address>` still runs a sync. (#4)
* Add `SettingsStore`: `/config/settings.json` (mode `0600`), seeded from env vars on first start, canonical afterwards. (#5)
* Add authentication: Argon2id passwords, first-run wizard, login rate limiting, and `login` / `none` / `proxy` modes. (#6)
* Add sync API with live progress over SSE. (#7)
* Add `serve`: Flask + waitress web service with an APScheduler-driven sync schedule. The `CRON` and `RUN_ONCE` env vars are retired; the image defaults to `serve` on port 8080. (#8)
* Add settings and auth APIs. (#9)
* Add structured JSON logs and Prometheus metrics export (upstream #73, #74).

## 2.2.0

* Replace undocumented `--filter` with `--include` and `--exclude` options for filtering recordings by type and direction. Codes are comma-separated, direction is optional. (#61)
* Add `--retry-failed-after` option to retry failed downloads after a configurable delay. (#58)
* Add `--skip-metadata` option to skip downloading metadata files (thumbnails, accelerometer, GPS). (#14)
* Stream recording downloads in chunks to avoid buffering full files in memory.
* Close the lock file descriptor when lock acquisition fails and distinguish lock contention from other OS errors.
* Ensure lock descriptor `0` is always unlocked on exit.

## 2.1.1

* Switch to semver.

## 2.1

* Minor resource cleanup fix. (#52)

## 2.0

* Modernize for Python 3.9, now that it's available in Debian Bullseye oldoldstable, the earliest LTS-supported Debian release. Now uses type hints, f-strings; walrus operator.
* Logging uses lazy evaluation.
* Add initial Claude Code settings and AI contribution policy.
* Build Docker images for amd64, arm64, and armv7 architectures. (#12)
* Add support for 'O' (Optional) camera direction on DR770X Box Pro and similar models. (inspired by grysage/blackvuesync)
* Add support for DMS (Driver Monitoring System) recording types: D (Drowsiness), L (Distraction), Y (Seatbelt), F (Undetected).
* Publish to PyPi. Can be run with `uvx blackvuesync` without explicitly installing.
* Introduce integration tests for some features.

## 1.10 (2025-12-28)

* Add `--filter` option to filter which events are downloaded. (#6)
* Add support for the interior camera found on the DR750X-3CH. (#7)
* Download GPS data for all recording types. (#9)
* Flush logs on exit. (#20)
* Silence host/network down/unreachable and timeout in cron mode (inspired by #23).
* Propagate exit status code to calling process. In cron mode, expected errors produce a success exit status.
* Add "rdate" priority, to download from newest to oldest.
* Upgrade alpine image to 3.23.2.

## 1.9 (2021-08-08)

* Properly removes outdated recordings with new event types and upload flags from May 2021 firmware. (#4)

## 1.8 (2021-05-24)

* Supports new event types produced by the May 2021 [BlackVue firmware update](https://blackvue.com/major-update-improved-blackvue-app-ui-dark-mode-live-event-upload-and-more/). (#3)
* The Docker image respects the KEEP option now.
* Docker compose file for a possibly quicker quickstart.
* Friendlier hardware requirement descriptions. (#2)
* More reliable removal of outdated directories when grouping by day, month or year.
* Better handling of unexpected 500 errors or remote disconnections.
* Upgraded docker image to alpine 3.13.5

## 1.7 (2019-07-14)

* Allows grouping recordings by date, with daily, weekly, monthly and yearly granularities.
* Docker image layers are more cacheable.
* Upgraded docker image to alpine 3.10.1

## 1.6 (2019-06-01)

* Logs file/recording download speed to help troubleshoot unreliable/slow Wi-Fi setups.
* Fixed a spurious error log during the first run after midnight.
* Does a better job at cleaning up temp files from interrupted downloads.
* Better handling of network errors while reading the file list.

## 1.5 (2019-03-03)

* Downloads .thm (thumbnail) files for all recordings.
* New ``--priority` switch allows downloading by either a) date or b) type (manual, event, normal, parking in that order.)
* Now downloads front and rear recordings together.

## 1.4 (2019-02-26)

* Downloads gps data for all but parking recording types, and accelerometer data for all.
* 500 errors while downloading are logged but ignored, so we don't get stuck on files we can't download.
* Tests that outdated gps/accelerometer files exist before deleting them, so it doesn't error out.

## 1.3 (2019-02-09)

* Removes gps data for outdated recordings along with the video.
* Gracefully handles low-level socket timeouts.

## 1.2 (2019-02-02)

* Removes temporary files upon successful completion.

## 1.1 (2019-02-01)

* No more sporadically getting stuck forever trying to connect to the dashcam.
* Connection timeout defaults to 10 seconds and is configurable.

## 1.0 (2019-01-30)

* initial release
