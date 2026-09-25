#!/usr/bin/env bash

if [[ $PUID -gt 0 ]]; then
    usermod -o -u "$PUID" dashcam
fi

if [[ $PGID -gt 0 ]]; then
    groupmod -o -g "$PGID" dashcam
fi

# creates /config and hands it to the (possibly remapped) dashcam user so the
# settings file, stats database and logs can be written. only the directory
# itself is chowned; existing files keep their ownership.
mkdir -p /config
chown dashcam:dashcam /config
