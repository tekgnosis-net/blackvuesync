#!/usr/bin/env bash
# entrypoint: remaps the dashcam user to PUID/PGID if set, then execs the
# long-running web service as the dashcam user via su-exec (Alpine's lighter
# alternative to gosu).
set -eu

/setuid.sh

exec su-exec dashcam python -m blackvuesync serve
