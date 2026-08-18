"""
Launch the multi-tank PLC/SCADA simulation server.

Usage:
    python run_scada.py [--host 127.0.0.1] [--port 8000] [--speed 1.0]

Then open http://127.0.0.1:8000 in a browser.
"""

import argparse

import uvicorn


def main() -> None:
    from scada import config

    parser = argparse.ArgumentParser(description="Multi-tank PLC/SCADA simulator")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--speed", type=float, default=1.0,
                        help="simulation speed multiplier (1.0 = real time)")
    parser.add_argument("--modbus-port", type=int, default=config.MODBUS_PORT,
                        help="Modbus TCP listen port (default 502)")
    parser.add_argument("--no-modbus", action="store_true",
                        help="disable the Modbus TCP field interface")
    args = parser.parse_args()

    # Apply overrides before the app is created.
    config.SIM_SPEED = max(0.01, args.speed)
    config.MODBUS_PORT = max(1, args.modbus_port)
    if args.no_modbus:
        config.MODBUS_ENABLED = False

    uvicorn.run("scada.server:app", host=args.host, port=args.port,
                log_level="info")


if __name__ == "__main__":
    main()
