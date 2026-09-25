"""enumerates downloaded recordings and computes auto-advance journey chains.

a recording instant is keyed by (base_filename, type); front/rear .mp4 share it
and differ by direction, and share one .gps/.3gf. an .mp4 may carry an upload
flag (`_NFL.mp4`), so the actual on-disk video/thumbnail filenames are kept per
direction; sync.py stores the .thm/.gps/.3gf without the flag. built on
sync.to_recording.
"""

from __future__ import annotations

import dataclasses
import datetime
import os

from blackvuesync.sync import to_recording

# two same-type segments are part of one journey when the next starts within
# this window of the prior (blackvue writes ~1-minute back-to-back segments).
_CONTIGUOUS_GAP = datetime.timedelta(seconds=120)


@dataclasses.dataclass(frozen=True)
class RecordingEntry:
    """one recording instant (base_filename + type) with its available artifacts."""

    # pylint: disable=too-many-instance-attributes
    base_filename: str
    type: str
    datetime: datetime.datetime
    directions: tuple[str, ...]
    has_gps: bool
    has_3gf: bool
    has_thm: bool
    rel_dir: str  # directory relative to destination ("" when ungrouped)
    # (direction, filename) pairs of the on-disk .mp4 / .thm, sorted by direction
    video_files: tuple[tuple[str, str], ...] = ()
    thumb_files: tuple[tuple[str, str], ...] = ()


@dataclasses.dataclass
class _InstantSlot:
    """accumulates per-direction .mp4 filenames for one recording instant."""

    dt: datetime.datetime
    videos: dict[str, list[str]] = dataclasses.field(default_factory=dict)


def _pick_video(names: list[str]) -> str:
    """picks one .mp4 per direction, preferring the unflagged (shortest) name."""
    return min(names, key=lambda n: (len(n), n))


def _thumb_for(rel_dir: str, video: str, plain_stem: str, present: set[str]) -> str:
    """returns the .thm filename for a video, or "" when none is on disk.

    sync.py names the thumbnail without the upload flag; a flagged thumbnail
    (same stem as the video) is also accepted.
    """
    for stem in dict.fromkeys((plain_stem, video[: -len(".mp4")])):
        if os.path.join(rel_dir, f"{stem}.thm") in present:
            return f"{stem}.thm"
    return ""


def _build_entry(
    rel_dir: str,
    base: str,
    rtype: str,
    slot: _InstantSlot,
    present: set[str],
) -> RecordingEntry:
    """constructs a RecordingEntry from a collected slot and present-file set."""
    dirs = sorted(slot.videos)
    videos = tuple((d, _pick_video(slot.videos[d])) for d in dirs)
    thumbs = tuple(
        (d, thm)
        for d, video in videos
        if (thm := _thumb_for(rel_dir, video, f"{base}_{rtype}{d}", present))
    )
    return RecordingEntry(
        base_filename=base,
        type=rtype,
        datetime=slot.dt,
        directions=tuple(dirs),
        has_gps=os.path.join(rel_dir, f"{base}_{rtype}.gps") in present,
        has_3gf=os.path.join(rel_dir, f"{base}_{rtype}.3gf") in present,
        has_thm=bool(thumbs),
        rel_dir=rel_dir,
        video_files=videos,
        thumb_files=thumbs,
    )


def list_recordings(destination: str, grouping: str) -> list[RecordingEntry]:
    """walks destination and returns recording instants, newest first."""
    if not os.path.isdir(destination):
        return []

    # group .mp4 files by (rel_dir, base_filename, type) -> _InstantSlot
    grouped: dict[tuple[str, str, str], _InstantSlot] = {}
    present: set[str] = set()
    for root, _dirs, files in os.walk(destination):
        rel_dir = os.path.relpath(root, destination)
        rel_dir = "" if rel_dir == "." else rel_dir
        for name in files:
            present.add(os.path.join(rel_dir, name))
            rec = to_recording(name, grouping)
            if rec is None:
                continue  # only .mp4 names match to_recording
            key = (rel_dir, rec.base_filename, rec.type)
            slot = grouped.setdefault(key, _InstantSlot(dt=rec.datetime))
            slot.videos.setdefault(rec.direction, []).append(name)

    entries = [
        _build_entry(rel_dir, base, rtype, slot, present)
        for (rel_dir, base, rtype), slot in grouped.items()
    ]
    entries.sort(key=lambda e: (e.datetime, e.base_filename, e.rel_dir), reverse=True)
    return entries


def journey_chain(
    entries: list[RecordingEntry], base_filename: str, rtype: str
) -> list[RecordingEntry]:
    """returns the forward chain of contiguous same-type segments from a start."""
    same_type = sorted(
        (e for e in entries if e.type == rtype), key=lambda e: e.datetime
    )
    chain: list[RecordingEntry] = []
    started = False
    prev: RecordingEntry | None = None
    for entry in same_type:
        if not started:
            if entry.base_filename == base_filename:
                started, prev, chain = True, entry, [entry]
            continue
        assert prev is not None
        gap = (entry.datetime - prev.datetime).total_seconds()
        if 0 < gap <= _CONTIGUOUS_GAP.total_seconds():
            chain.append(entry)
            prev = entry
        else:
            break
    return chain


__all__ = ["RecordingEntry", "journey_chain", "list_recordings"]
