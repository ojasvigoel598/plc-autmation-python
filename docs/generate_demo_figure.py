"""Generate the README demo figure from a real simulation run.

Run:  python docs/generate_demo_figure.py
Output: docs/demo_response.png
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from scada import config
from scada.runtime import Runtime


def main() -> None:
    rt = Runtime()
    rt.plc.pulse_start()

    def run(seconds):
        for _ in range(int(seconds / config.PLC_SCAN_DT)):
            rt.step()

    run(120.0)                                     # settle at SP=0.8
    rt.plc.set_setpoint("LIC-102", 1.0)
    run(90.0)                                      # setpoint step response
    rt.inject_disturbance("TK-102", 0.004, 40.0)
    run(90.0)                                      # disturbance + recovery

    hist = list(rt.history)
    t = [d["t"] for d in hist]

    fig, axes = plt.subplots(3, 1, figsize=(11, 8), sharex=True)
    fig.suptitle("Multi-tank cascade: setpoint step and disturbance rejection",
                 fontsize=13)

    ax = axes[0]
    ax.plot(t, [d["h1"] for d in hist], label="TK-101 (LIC-101)", lw=1.6)
    ax.plot(t, [d["h2"] for d in hist], label="TK-102 (LIC-102)", lw=1.6)
    ax.plot(t, [d["h3"] for d in hist], label="TK-103", lw=1.6)
    ax.plot(t, [d["lic102_sp"] for d in hist], "--", color="gray", lw=1.2,
            label="LIC-102 setpoint")
    ax.set_ylabel("level (m)")
    ax.legend(loc="upper left", fontsize=8, ncol=2)
    ax.grid(alpha=0.3)

    ax = axes[1]
    ax.plot(t, [d["lic102_mv"] for d in hist], label="LIC-102 output (XV-101)", lw=1.6)
    ax.plot(t, [d["lic101_mv"] for d in hist], label="LIC-101 output (P-101)", lw=1.6)
    ax.set_ylabel("controller output (%)")
    ax.set_ylim(-5, 105)
    ax.legend(loc="upper left", fontsize=8)
    ax.grid(alpha=0.3)

    ax = axes[2]
    ax.plot(t, [d["pump_flow"] * 1000 for d in hist], label="P-101", lw=1.4)
    ax.plot(t, [d["v101_flow"] * 1000 for d in hist], label="XV-101", lw=1.4)
    ax.plot(t, [d["v102_flow"] * 1000 for d in hist], label="XV-102", lw=1.4)
    ax.plot(t, [d["v103_flow"] * 1000 for d in hist], label="XV-103", lw=1.4)
    ax.set_ylabel("flow (L/s)")
    ax.set_xlabel("time (s)")
    ax.legend(loc="upper left", fontsize=8, ncol=2)
    ax.grid(alpha=0.3)

    out = Path(__file__).parent / "demo_response.png"
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    print("saved", out)


if __name__ == "__main__":
    main()
