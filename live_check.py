"""Tracked live results vs backtested expectations.

The test suite (tests/test_robustness.py) is the *backtest*: it defines the
honest performance envelope (settled error < 0.05 m for reachable setpoints
under noise, disturbance and plant perturbation).  This script is the
*live* side of the validation loop: it drives the same scenario against a
running SCADA server over the REST API, measures what actually happened,
and compares the measurements with the backtested envelope.

Run it periodically (cron / scheduled task) and watch the appended history
in ``data/live_check_history.jsonl`` for signs of edge decay — e.g. a
reservoir that has drained (the finite-reservoir model) or a plant that has
drifted from the tuned model.  Each run writes one JSON line:

    {"ts": <wall clock>, "step": {"end_error": .., "max_dev": ..},
     "disturbance": {"end_error": .., "max_dev": ..},
     "envelope": {"settle_tol": 0.05}, "pass": true|false}

Usage:
    python live_check.py [--url http://127.0.0.1:8000]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Matches the out-of-sample envelope in tests/test_robustness.py.
SETTLE_TOL = 0.05
STEP_SP = 1.0          # m, on LIC-102 (within the reachable region)
DIST_FLOW = 0.008      # m^3/s, sustained 30 s on TK-102

DATA_DIR = Path(__file__).resolve().parent / "data"
HISTORY_FILE = DATA_DIR / "live_check_history.jsonl"


def _api(base: str, path: str, body: dict | None = None,
         method: str | None = None, timeout: float = 10.0):
    url = base + path
    if method is None:
        method = "POST" if body is not None else "GET"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if body is not None else {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _state(base: str) -> dict:
    return _api(base, "/api/state")


def _level(rt: dict) -> float:
    return rt["levels"]["TK-102"]


def _mean_abs_err(samples: list[tuple[float, float]], sp: float) -> float:
    """Mean |level - setpoint| over the sample window."""
    if not samples:
        return float("inf")
    return sum(abs(level - sp) for _, level in samples) / len(samples)


def _run_scenario(base: str) -> dict:
    # Reset to a known state and start the plant.
    _api(base, "/api/control/estop", {"active": False})
    _api(base, "/api/control/reset", method="POST")
    _api(base, "/api/control/start", method="POST")
    _api(base, "/api/control/setpoint", {"tag": "LIC-102", "value": 0.8})

    # Wait for the plant to reach RUNNING and settle at the baseline.
    t0 = time.monotonic()
    while time.monotonic() - t0 < 30:
        rt = _state(base)
        if rt["state"] == "RUNNING":
            break
        time.sleep(1.0)
    if _state(base)["state"] != "RUNNING":
        raise RuntimeError("plant did not reach RUNNING on the live server")
    time.sleep(20)  # let the baseline settle

    # --- Setpoint step (out-of-sample vs the in-sample 0.8 baseline) -----
    _api(base, "/api/control/setpoint", {"tag": "LIC-102", "value": STEP_SP})
    step_samples: list[tuple[float, float]] = []
    t_end = time.monotonic() + 100
    while time.monotonic() < t_end:
        rt = _state(base)
        step_samples.append((time.monotonic(), _level(rt)))
        time.sleep(1.0)
    step_end = _mean_abs_err(step_samples[-10:], STEP_SP)
    step_max_dev = max(abs(l - STEP_SP) for _, l in step_samples)

    # --- Return to baseline, then disturbance + recovery -----------------
    _api(base, "/api/control/setpoint", {"tag": "LIC-102", "value": 0.8})
    time.sleep(30)
    _api(base, "/api/faults/disturbance",
         {"tank": "TK-102", "flow_m3s": DIST_FLOW, "duration": 30})
    dist_samples: list[tuple[float, float]] = []
    t_end = time.monotonic() + 90
    while time.monotonic() < t_end:
        rt = _state(base)
        dist_samples.append((time.monotonic(), _level(rt)))
        time.sleep(1.0)
    dist_end = _mean_abs_err(dist_samples[-10:], 0.8)
    dist_max_dev = max(abs(l - 0.8) for _, l in dist_samples)

    # Leave the plant at a sane operating point (SP back to default 0.8).
    _api(base, "/api/control/setpoint", {"tag": "LIC-102", "value": 0.8})

    return {
        "step": {"end_error": round(step_end, 4),
                 "max_dev": round(step_max_dev, 4)},
        "disturbance": {"end_error": round(dist_end, 4),
                        "max_dev": round(dist_max_dev, 4)},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000",
                        help="base URL of the running SCADA server")
    args = parser.parse_args(argv)

    print(f"Live check against {args.url} "
          f"(envelope: settled error < {SETTLE_TOL} m) ...")
    result = _run_scenario(args.url)

    ok = (result["step"]["end_error"] < SETTLE_TOL
          and result["disturbance"]["end_error"] < SETTLE_TOL)
    report = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "url": args.url,
        "scenario": {"setpoint_step_m": STEP_SP,
                     "disturbance_flow_m3s": DIST_FLOW},
        **result,
        "envelope": {"settle_tol": SETTLE_TOL},
        "pass": ok,
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(report) + "\n")

    print(f"  setpoint step  -> end error {report['step']['end_error']:+.4f} m "
          f"(max dev {report['step']['max_dev']:.4f})")
    print(f"  disturbance    -> end error {report['disturbance']['end_error']:+.4f} m "
          f"(max dev {report['disturbance']['max_dev']:.4f})")
    print(f"  RESULT: {'PASS — within backtested envelope' if ok else 'FAIL — outside backtested envelope'}")
    print(f"  history appended to {HISTORY_FILE}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
