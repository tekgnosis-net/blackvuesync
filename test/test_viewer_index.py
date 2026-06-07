"""unit tests for recording enumeration + journey chaining."""

from __future__ import annotations

from pathlib import Path

from blackvuesync.server.viewer_index import (
    RecordingEntry,
    journey_chain,
    list_recordings,
)


def _touch(root: Path, name: str) -> None:
    (root / name).write_bytes(b"x")


def test_groups_directions_and_detects_sidecars(tmp_path: Path) -> None:
    _touch(tmp_path, "20260607_101500_NF.mp4")
    _touch(tmp_path, "20260607_101500_NR.mp4")
    _touch(tmp_path, "20260607_101500_N.gps")
    _touch(tmp_path, "20260607_101500_NF.thm")
    entries = list_recordings(str(tmp_path), "none")
    assert len(entries) == 1
    e = entries[0]
    assert isinstance(e, RecordingEntry)
    assert e.base_filename == "20260607_101500"
    assert e.type == "N"
    assert e.directions == ("F", "R")
    assert e.has_gps is True
    assert e.has_3gf is False
    assert e.has_thm is True
    assert e.rel_dir == ""


def test_newest_first_and_grouping_subdir(tmp_path: Path) -> None:
    day = tmp_path / "2026-06-07"
    day.mkdir()
    _touch(day, "20260607_101500_NF.mp4")
    _touch(day, "20260607_101600_NF.mp4")
    entries = list_recordings(str(tmp_path), "daily")
    assert [e.base_filename for e in entries] == ["20260607_101600", "20260607_101500"]
    assert entries[0].rel_dir == "2026-06-07"


def test_journey_chain_links_contiguous_same_type_only() -> None:
    def entry(ts: str, typ: str = "N") -> RecordingEntry:
        import datetime

        dt = datetime.datetime.strptime(ts, "%Y%m%d_%H%M%S")
        return RecordingEntry(ts, typ, dt, ("F",), False, False, False, "")

    a, b, c = (
        entry("20260607_101500"),
        entry("20260607_101600"),
        entry("20260607_101700"),
    )
    far = entry("20260607_120000")  # >2 min later -> breaks the chain
    parking = entry("20260607_101800", "P")  # different type -> not chained
    chain = journey_chain([a, b, c, far, parking], "20260607_101500", "N")
    assert [e.base_filename for e in chain] == [
        "20260607_101500",
        "20260607_101600",
        "20260607_101700",
    ]


def test_journey_chain_start_not_found_returns_empty() -> None:
    import datetime

    dt = datetime.datetime(2026, 6, 7, 10, 15, 0)
    e = RecordingEntry("20260607_101500", "N", dt, ("F",), False, False, False, "")
    assert journey_chain([e], "20260607_999999", "N") == []


def test_journey_chain_gap_boundary_inclusive_at_120s() -> None:
    import datetime

    base = datetime.datetime(2026, 6, 7, 10, 15, 0)

    def at(seconds: int) -> RecordingEntry:
        return RecordingEntry(
            f"ts_{seconds}",
            "N",
            base + datetime.timedelta(seconds=seconds),
            ("F",),
            False,
            False,
            False,
            "",
        )

    # ts_120 is exactly at the 120-second boundary from ts_0 -- included.
    # ts_241 is 121 seconds after ts_120 -- exceeds the gap window, breaks the
    # chain.
    chain = journey_chain([at(0), at(120), at(241)], "ts_0", "N")
    assert [e.base_filename for e in chain] == ["ts_0", "ts_120"]
