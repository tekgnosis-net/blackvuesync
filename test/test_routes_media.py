"""tests for the path-safe /media file route."""

from __future__ import annotations

import dataclasses
import os
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
    (dest / "20260607_101500_NF.mp4").write_bytes(b"video-bytes-here")
    (dest / "20260607_101500_NF.thm").write_bytes(b"\xff\xd8\xff\xe0jpeg")
    (dest / "secret.json").write_text("{}")
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


def test_serves_mp4(client_and_dest: Any) -> None:
    client, _ = client_and_dest
    resp = client.get("/media/20260607_101500_NF.mp4")
    assert resp.status_code == 200
    assert resp.data == b"video-bytes-here"


def test_thm_served_as_jpeg(client_and_dest: Any) -> None:
    client, _ = client_and_dest
    resp = client.get("/media/20260607_101500_NF.thm")
    assert resp.status_code == 200
    assert resp.mimetype == "image/jpeg"


def test_range_request_returns_206(client_and_dest: Any) -> None:
    client, _ = client_and_dest
    resp = client.get("/media/20260607_101500_NF.mp4", headers={"Range": "bytes=0-4"})
    assert resp.status_code == 206
    assert resp.data == b"video"


def test_traversal_rejected(client_and_dest: Any) -> None:
    client, _ = client_and_dest
    assert client.get("/media/../settings.json").status_code == 404
    assert client.get("/media/%2e%2e%2fsettings.json").status_code == 404


def test_disallowed_extension_rejected(client_and_dest: Any) -> None:
    client, _ = client_and_dest
    assert client.get("/media/secret.json").status_code == 404


def test_requires_login(client_and_dest: Any) -> None:
    _, dest = client_and_dest
    with patch.dict(os.environ, {"ADDRESS": "1.2.3.4"}, clear=False):
        anon = create_app(SettingsStore(dest.parent / "settings.json"), testing=True)
    resp = anon.test_client().get("/media/20260607_101500_NF.mp4")
    assert resp.status_code in (302, 401)
