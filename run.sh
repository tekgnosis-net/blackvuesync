#!/usr/bin/env bash

# overrides the default CMD (`serve`) with a one-shot `sync` invocation so this
# smoke test exits after a single sync attempt, replacing the retired RUN_ONCE=1
# entrypoint shortcut. the entrypoint prepends `python -m blackvuesync` to
# whatever CMD is passed, so only the subcommand and its args go on the command
# line below.
docker run -it --rm \
    -e ADDRESS=dashcam-porsche.peanuts.ink \
    -v "$(pwd)"/tmp:/recordings \
    -e DRY_RUN=1 \
    -e VERBOSE=1 \
    --name blackvuesync \
    ghcr.io/tekgnosis-net/blackvuesync \
    sync --dry-run dashcam-porsche.peanuts.ink --destination /recordings
