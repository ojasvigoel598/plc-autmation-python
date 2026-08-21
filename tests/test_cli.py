"""CLI tests: single-process guard and port default."""

import pytest

from scada.cli import build_parser, main


def test_port_default_falls_back_when_port_env_is_zero(monkeypatch):
    monkeypatch.setenv("PORT", "0")
    assert build_parser().parse_args([]).port == 8000


def test_port_env_is_honoured(monkeypatch):
    monkeypatch.setenv("PORT", "10000")
    assert build_parser().parse_args([]).port == 10000


def test_explicit_port_wins_over_env(monkeypatch):
    monkeypatch.setenv("PORT", "10000")
    assert build_parser().parse_args(["--port", "8000"]).port == 8000


def test_multiple_workers_rejected(monkeypatch):
    """The simulator owns module-level state: more than one uvicorn worker
    would fork separate plants, so the CLI must refuse it loudly."""
    with pytest.raises(SystemExit) as exc:
        main(["--workers", "2"])
    assert "single-process" in str(exc.value)
