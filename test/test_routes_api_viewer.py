"""tests for the /api/viewer JSON endpoints."""

from __future__ import annotations

import dataclasses
import json
import os
import struct
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from blackvuesync.server import create_app
from blackvuesync.server.auth import hash_password
from blackvuesync.settings import SettingsStore


@pytest.fixture()
def client_and_dest(tmp_path: Path):  # type: ignore[no-untyped-def]
    dest = tmp_path / "recordings"
    dest.mkdir()
    for name in (
        "20260607_101500_NF.mp4",
        "20260607_101500_NR.mp4",
        "20260607_101600_NF.mp4",
    ):
        (dest / name).write_bytes(b"x")
    (dest / "20260607_101500_N.gps").write_text(
        "[1000]$GNRMC,055056.00,A,3348.10000,S,15101.10000,E,0.000,,070626,,,A,V*06\r\n"
    )
    (dest / "20260607_101500_N.3gf").write_bytes(struct.pack(">Ihhh", 0, 130, 5, -20))
    with patch.dict(os.environ, {"ADDRESS": "1.2.3.4"}, clear=False):
        store = SettingsStore(tmp_path / "settings.json")
    store.update(
        lambda s: dataclasses.replace(
            s,
            auth=dataclasses.replace(
                s.auth, password_hash=hash_password("pw-1234-test")
            ),
            system=dataclasses.replace(s.system, destination=str(dest)),
        )
    )
    app = create_app(store, testing=True)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user"] = "admin"
    return client, dest


def test_recordings_grouped_newest_first(client_and_dest: Any) -> None:
    client, _ = client_and_dest
    body = json.loads(client.get("/api/viewer/recordings").data)
    bases = [r["base_filename"] for day in body["days"] for r in day["recordings"]]
    assert bases == ["20260607_101600", "20260607_101500"]
    first = next(
        r
        for day in body["days"]
        for r in day["recordings"]
        if r["base_filename"] == "20260607_101500"
    )
    assert first["directions"] == ["F", "R"]
    assert first["has_gps"] is True and first["has_3gf"] is True


def test_journey_chain(client_and_dest: Any) -> None:
    client, _ = client_and_dest
    body = json.loads(
        client.get("/api/viewer/recordings/20260607_101500_N/journey").data
    )
    assert [s["base_filename"] for s in body["segments"]] == [
        "20260607_101500",
        "20260607_101600",
    ]


def test_gps_and_gsensor_json(client_and_dest: Any) -> None:
    client, _ = client_and_dest
    gps = json.loads(client.get("/api/viewer/recordings/20260607_101500_N/gps").data)
    assert gps["points"][0]["lat"] == pytest.approx(-(33 + 48.1 / 60))
    g = json.loads(client.get("/api/viewer/recordings/20260607_101500_N/gsensor").data)
    assert g["samples"][0]["x"] == 130 / 128.0


def test_requires_login(client_and_dest: Any) -> None:
    _, dest = client_and_dest
    with patch.dict(os.environ, {"ADDRESS": "1.2.3.4"}, clear=False):
        anon = create_app(SettingsStore(dest.parent / "s2.json"), testing=True)
    assert anon.test_client().get("/api/viewer/recordings").status_code in (302, 401)
