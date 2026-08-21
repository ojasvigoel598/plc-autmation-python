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


def test_multiple_workers_rejected_without_ha(monkeypatch):
    """Without HA, the simulator owns module-level state: more than one
    uvicorn worker would fork separate plants, so the CLI must refuse it
    loudly and point the operator at --ha."""
    monkeypatch.delenv("SCADA_HA", raising=False)
    with pytest.raises(SystemExit) as exc:
        main(["--workers", "2"])
    assert "--ha" in str(exc.value)


def test_multiple_workers_allowed_with_ha_flag(monkeypatch):
    """With --ha, multiple workers are meaningful: exactly one acquires the
    lease and runs the simulation; the rest serve the checkpoint read-only."""
    monkeypatch.delenv("SCADA_HA", raising=False)
    args = build_parser().parse_args(["--workers", "2", "--ha"])
    assert args.workers == 2
    assert args.ha is True


def test_ha_db_flag_sets_config(monkeypatch):
    from scada import config
    monkeypatch.delenv("SCADA_HA", raising=False)
    args = build_parser().parse_args(["--ha", "--ha-db", "/tmp/ha.sqlite3"])
    assert args.ha_db == "/tmp/ha.sqlite3"
