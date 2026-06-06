"""unit tests for the disk-usage forecast."""

from __future__ import annotations

from blackvuesync.server.forecast import Forecast, compute_forecast


def _rising() -> list[tuple[float, float]]:
    # ts in seconds, ratio rising 0.40 -> 0.50 over 5 points one day apart
    return [(i * 86400.0, 0.40 + i * 0.025) for i in range(5)]


def test_too_few_points_no_projection() -> None:
    fc = compute_forecast(
        [(0.0, 0.4), (86400.0, 0.42)],
        horizon_seconds=7 * 86400.0,
        steps=7,
        max_used_disk_ratio=0.90,
        keep_steady_state_ratio=None,
    )
    assert isinstance(fc, Forecast)
    assert fc.projected == []
    assert fc.max_used_disk_percent == 0.90


def test_projection_rises_and_clamps_to_ceiling() -> None:
    fc = compute_forecast(
        _rising(),
        horizon_seconds=30 * 86400.0,
        steps=6,
        max_used_disk_ratio=0.55,
        keep_steady_state_ratio=None,
    )
    assert fc.projected, "expected projected points"
    ys = [y for _, y in fc.projected]
    assert ys == sorted(ys)  # non-decreasing
    assert max(ys) <= 0.55 + 1e-9  # clamped at the binding ceiling


def test_binding_ceiling_is_the_lower_of_two() -> None:
    fc = compute_forecast(
        _rising(),
        horizon_seconds=60 * 86400.0,
        steps=4,
        max_used_disk_ratio=0.90,
        keep_steady_state_ratio=0.52,
    )
    assert max(y for _, y in fc.projected) <= 0.52 + 1e-9


def test_projection_bounded_to_unit_interval() -> None:
    pts = [(i * 86400.0, 0.95 + i * 0.05) for i in range(5)]  # would exceed 1.0
    fc = compute_forecast(
        pts,
        horizon_seconds=10 * 86400.0,
        steps=5,
        max_used_disk_ratio=None,
        keep_steady_state_ratio=None,
    )
    assert all(0.0 <= y <= 1.0 for _, y in fc.projected)


def test_none_ratios_are_skipped_in_fit() -> None:
    pts = [(0.0, None), (86400.0, 0.4), (2 * 86400.0, 0.42), (3 * 86400.0, 0.44)]
    fc = compute_forecast(
        pts,
        horizon_seconds=7 * 86400.0,
        steps=3,
        max_used_disk_ratio=0.9,
        keep_steady_state_ratio=None,
    )
    assert fc.projected  # 3 valid points -> projection produced
