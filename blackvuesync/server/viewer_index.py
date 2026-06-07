"""enumerates downloaded recordings and computes auto-advance journey chains.

a recording instant is keyed by (base_filename, type); front/rear .mp4 share it
and differ by direction, and share one .gps/.3gf. built on sync.to_recording.
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


@dataclasses.dataclass
class _InstantSlot:
    """accumulates directions while scanning for one recording instant."""

    dt: datetime.datetime
    dirs: set[str] = dataclasses.field(default_factory=set)


def _build_entry(
    rel_dir: str,
    base: str,
    rtype: str,
    slot: _InstantSlot,
    present: set[str],
) -> RecordingEntry:
    """constructs a RecordingEntry from a collected slot and present-file set."""
    dirs = sorted(slot.dirs)
    return RecordingEntry(
        base_filename=base,
        type=rtype,
        datetime=slot.dt,
        directions=tuple(dirs),
        has_gps=os.path.join(rel_dir, f"{base}_{rtype}.gps") in present,
        has_3gf=os.path.join(rel_dir, f"{base}_{rtype}.3gf") in present,
        has_thm=any(
            os.path.join(rel_dir, f"{base}_{rtype}{d}.thm") in present for d in dirs
        ),
        rel_dir=rel_dir,
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
            slot.dirs.add(rec.direction)

    entries = [
        _build_entry(rel_dir, base, rtype, slot, present)
        for (rel_dir, base, rtype), slot in grouped.items()
    ]
    entries.sort(key=lambda e: e.datetime, reverse=True)
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
