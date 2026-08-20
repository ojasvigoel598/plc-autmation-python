"""Command-line entry point for the SCADA simulator.

Exposed as the ``plc-scada-sim`` console script (and kept callable as
``python -m scada.cli``).  ``run_scada.py`` at the repository root delegates
here so there is exactly one CLI implementation.
"""

from __future__ import annotations

import argparse
import os

import uvicorn


def _default_port() -> int:
    """Honour the standard ``PORT`` env var used by PaaS hosts (Render, Fly.io,
    Railway, Heroku) so the same image deploys anywhere without a custom start
    command.  Falls back to 8000 for local runs or when PORT is unset/0.
    """
    try:
        port = int(os.environ.get("PORT", "0"))
    except ValueError:
        port = 0
    return port if port > 0 else 8000


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Multi-tank PLC/SCADA simulator")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=_default_port())
    parser.add_argument("--speed", type=float, default=1.0,
                        help="simulation speed multiplier (1.0 = real time)")
    parser.add_argument("--modbus-port", type=int, default=None,
                        help="Modbus TCP listen port (default 502)")
    parser.add_argument("--no-modbus", action="store_true",
                        help="disable the Modbus TCP field interface")
    return parser


def main(argv: list[str] | None = None) -> None:
    from scada import config

    args = build_parser().parse_args(argv)

    # Apply overrides before the app is created.
    config.SIM_SPEED = max(0.01, args.speed)
    if args.modbus_port is not None:
        config.MODBUS_PORT = max(1, args.modbus_port)
    if args.no_modbus:
        config.MODBUS_ENABLED = False

    uvicorn.run("scada.server:app", host=args.host, port=args.port,
                log_level="info")


if __name__ == "__main__":
    main()
