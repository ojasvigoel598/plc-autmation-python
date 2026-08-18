"""Launch the multi-tank PLC/SCADA simulation server.

Usage:
    python run_scada.py [--host 127.0.0.1] [--port 8000] [--speed 1.0]

Then open http://127.0.0.1:8000 in a browser.

This file is a thin wrapper around ``scada.cli.main`` so the repository-root
launcher and the installed ``plc-scada-sim`` console script share one CLI.
"""

from scada.cli import main

if __name__ == "__main__":
    main()
