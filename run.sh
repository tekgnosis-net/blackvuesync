#!/usr/bin/env bash

# overrides the default CMD (`python -m blackvuesync serve`) so this smoke
# test exits after a single sync attempt, replacing the retired RUN_ONCE=1
# entrypoint shortcut.
docker run -it --rm \
    -e ADDRESS=dashcam-porsche.peanuts.ink \
    -v "$(pwd)"/tmp:/recordings \
    -e DRY_RUN=1 \
    -e VERBOSE=1 \
    --name blackvuesync \
    ghcr.io/tekgnosis-net/blackvuesync \
    python -m blackvuesync sync --dry-run dashcam-porsche.peanuts.ink --destination /recordings
