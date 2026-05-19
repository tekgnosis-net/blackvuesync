"""tests that cmd_serve wires up logging on startup."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture()
def settings_path(tmp_path: Path) -> Path:
    """returns a settings file path inside tmp_path."""
    return tmp_path / "settings.json"


def test_cmd_serve_configures_logging(settings_path: Path) -> None:
    """cmd_serve must call configure_logging so scheduler / waitress info
    messages are visible in docker logs and the log-viewer."""
    from blackvuesync.__main__ import cmd_serve

    args = argparse.Namespace(
        port=None,
        config_path=str(settings_path),
    )

    with (
        patch.dict(os.environ, {"ADDRESS": "192.168.0.1"}, clear=False),
        patch("blackvuesync.__main__.configure_logging") as mock_cfg,
        patch("blackvuesync.__main__.set_logging_levels") as mock_set,
        patch("waitress.serve") as mock_waitress,
        patch("blackvuesync.server.scheduler.init_scheduler") as mock_init,
    ):
        mock_waitress.return_value = None
        cmd_serve(args)

    mock_cfg.assert_called_once()
    # cmd_serve calls set_logging_levels twice: once with defaults before
    # loading the settings store (so startup errors emit) and once after,
    # using the verbosity from settings.logging.
    assert mock_set.call_count == 2
    mock_init.assert_called_once()
    mock_waitress.assert_called_once()
