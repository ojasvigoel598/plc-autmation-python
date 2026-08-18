"""Command-line entry point for the SCADA simulator.

Exposed as the ``plc-scada-sim`` console script (and kept callable as
``python -m scada.cli``).  ``run_scada.py`` at the repository root delegates
here so there is exactly one CLI implementation.
"""

from __future__ import annotations

import argparse

import uvicorn


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Multi-tank PLC/SCADA simulator")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
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
