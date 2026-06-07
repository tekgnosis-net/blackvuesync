"""parser for blackvue .gps sidecar files: timestamped NMEA-0183 text.

format (see docs/reference/blackvue-file-formats.md): each line is
`[epoch-ms]$G?RMC,...` or `[epoch-ms]$G?GGA,...`. the talker is multi-gnss
($GN), so matching is talker-agnostic on the sentence type. stdlib-only.
"""

from __future__ import annotations

import dataclasses
import re
from decimal import Decimal

# [epoch-ms] + $ + G + any talker letter + RMC|GGA + comma + the field body.
_SENTENCE_RE = re.compile(
    r"\[(?P<ms>\d+)\]\$G[A-Z](?P<kind>RMC|GGA),(?P<fields>[^*\r\n]*)"
)


@dataclasses.dataclass(frozen=True)
class GpsPoint:
    """one GPS fix: elapsed seconds from start, decimal lat/lon, speed in knots."""

    t: float
    lat: float
    lon: float
    speed: float | None


def _dm_to_decimal(value: str, hemisphere: str) -> float | None:
    """converts a DDMM.mmmmm / DDDMM.mmmmm + hemisphere string to decimal degrees.

    uses Decimal arithmetic to avoid catastrophic cancellation when splitting
    degrees from minutes (e.g. 3348.1 - 3300 loses precision as float).
    """
    if not value:
        return None
    raw = Decimal(value)
    degrees = int(raw // 100)
    minutes = raw - degrees * 100
    decimal = float(degrees + minutes / 60)
    return -decimal if hemisphere in ("S", "W") else decimal


def _parse_rmc(fields: list[str]) -> tuple[float, float, float | None] | None:
    """returns (lat, lon, speed_knots) from RMC fields, or None when no fix."""
    # RMC: time, status, lat, N/S, lon, E/W, speed, course, date, ...
    if len(fields) < 7 or fields[1] != "A":
        return None
    lat = _dm_to_decimal(fields[2], fields[3])
    lon = _dm_to_decimal(fields[4], fields[5])
    if lat is None or lon is None:
        return None
    speed = float(fields[6]) if fields[6] else None
    return lat, lon, speed


def _parse_gga(fields: list[str]) -> tuple[float, float, float | None] | None:
    """returns (lat, lon, None) from GGA fields, or None when no fix."""
    # GGA: time, lat, N/S, lon, E/W, fix-quality, ...
    if len(fields) < 6 or fields[5] in ("", "0"):
        return None
    lat = _dm_to_decimal(fields[1], fields[2])
    lon = _dm_to_decimal(fields[3], fields[4])
    if lat is None or lon is None:
        return None
    return lat, lon, None


def parse_gps(text: str) -> list[GpsPoint]:
    """parses .gps text into GpsPoints, ascending by time, one per epoch-ms.

    RMC is preferred (it carries speed); a GGA-only timestamp is used as a
    position fallback. invalid / no-fix / unparseable lines are skipped.
    """
    by_ms: dict[int, tuple[float, float, float | None]] = {}
    rmc_ms: set[int] = set()
    for match in _SENTENCE_RE.finditer(text):
        ms = int(match.group("ms"))
        fields = match.group("fields").split(",")
        if match.group("kind") == "RMC":
            parsed = _parse_rmc(fields)
            if parsed is not None:
                by_ms[ms] = parsed
                rmc_ms.add(ms)
        elif ms not in rmc_ms:  # GGA only fills positions without an RMC
            parsed = _parse_gga(fields)
            if parsed is not None:
                by_ms[ms] = parsed
    if not by_ms:
        return []
    first_ms = min(by_ms)
    return [
        GpsPoint((ms - first_ms) / 1000.0, lat, lon, speed)
        for ms, (lat, lon, speed) in sorted(by_ms.items())
    ]


__all__ = ["GpsPoint", "parse_gps"]
