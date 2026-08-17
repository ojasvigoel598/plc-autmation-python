# CLAUDE.md — Multi-Tank PLC / SCADA Simulator

Agent instructions for working in this repository.  This is an
**engineering/control-systems project**, not just a Python app: read it with
a PLC/SCADA/control-engineering mindset.

## What this project is

A real-time, interactive simulation of a three-tank gravity cascade with a
software PLC engine, PID level control, fault injection, interlocks and a
live web SCADA dashboard.  Every control action must genuinely propagate
through the full chain:

```
plant -> sensors -> PLC input image -> control program -> PID
      -> output image -> actuators (faults applied) -> process response
```

Nothing may be cosmetically faked: if the UI shows a value, it must come
from the simulation.

## Repository layout

```
run_scada.py                 # entry point: uvicorn scada.server:app
scada/
  config.py                  # ALL engineering constants (SI units)
  process.py                 # plant model: tanks, valves, pump, transmitters, RK4
  pid.py                     # PID controller (anti-windup, bumpless, deriv-on-measurement)
  plc.py                     # IEC 61131-3 FBs, alarm manager, PLC scan engine
  runtime.py                 # field bus: sensors -> PLC -> actuators -> plant
  server.py                  # FastAPI: REST control + WebSocket telemetry
  static/                    # SCADA HMI (vanilla HTML/CSS/JS, no build step)
tests/                       # pytest: physics, PID, PLC, closed loop, API
docs/generate_demo_figure.py # regenerates docs/demo_response.png
main.py + legacy modules     # ORIGINAL matplotlib demo (superseded, keep intact)
```

## Engineering conventions (do not violate)

- **SI units everywhere.** Levels in metres, flows in m³/s (HMI shows L/s),
  head in metres of water.  No imperial units.
- **ISA-5.1 tag naming.** `P-101` pump, `XV-10x` motorised valves,
  `LT-10x` transmitters, `LIC-10x` controllers, `TK-10x` tanks.
- **Separated rates** (see `config.py`): 10 ms RK4 process sub-step,
  100 ms PLC scan/control, 100 ms UI push.  Never collapse them silently.
- **Scan-cycle discipline.** The PLC runs on a latched input image: the
  runtime copies transmitter readings in *before* `plc.scan(dt)` and applies
  outputs *after*.  A scan must not see its own outputs change mid-cycle.
- **IEC 61131-3 block semantics.** `TON`/`TOF`/`TP`/`CTU`/`CTD`/`SR`/`RS`
  must follow the standard (RS is reset-dominant, SR set-dominant; counters
  count rising edges only).
- **Interlocks beat everything.** E-stop > HIHI trips > loop outputs.
  Faults are latched until operator reset; fail-safe = pump off, valves closed.
- **Distinguish industrial concept from approximation.** When a model is a
  simplification (e.g. lumped orifice, ideal fluid, software timer instead of
  hardware scan), say so in a comment and in the README.

## Physics model (the short version)

```
A_i * dh_i/dt = sum(Q_in) - sum(Q_out) - Q_leak
Q_valve = u * Cd * Ao * sqrt(2 g dh)          # zero against adverse head
Q_pump  = u * Q_max * (1 - DROOP * head)      # centrifugal droop
actuators slew toward targets (finite stroke time)
overflow above tank height is spilled to drain and reported
```

Mass must balance: the test suite checks it.  Do not "fix" a level by
clamping; fix the flow physics.

## PID rules

Parallel ISA form `u = Kp e + Ki∫e + Kd de/dt`.  Required behaviours:
derivative on measurement (no setpoint kick), first-order derivative filter,
back-calculation anti-windup, output limits, bumpless manual↔auto transfer.
Changing Kp/Ki/Kd/SP/MV in the UI must change controller behaviour.

## Testing & verification (mandatory before finishing any change)

```bash
python -m pytest tests/ -q          # must pass (currently 45 tests)
python run_scada.py                 # server on http://127.0.0.1:8000
```

Verify at minimum: REST endpoints validate bad input (422), WebSocket
telemetry streams, a setpoint change is tracked, a disturbance is rejected,
E-stop forces safe state, a trip latches `FAULTED` and reset recovers it.
Run the acceptance scenario twice from a clean start to catch
intermittent/race issues.

## Workflow rules (repo owner requires these)

- **Commit every change** — small commits, one logical change each, with a
  descriptive message.
- **Push to GitHub** after committing:
  `git push origin main` (remote is ojasvigoel598/plc-autmation-python).
- **Security check before every commit**: no API keys, tokens, passwords,
  private URLs, `.env`, generated logs/plots (except `docs/demo_response.png`),
  or `__pycache__`/venvs.  `.gitignore` already covers most of these.
- Never force-push unless explicitly told to.
- Keep the README in sync with the actual implementation.

## Dependencies

Runtime: `numpy`, `fastapi`, `uvicorn`, `pydantic`, `websockets`,
`matplotlib` (legacy modules only).  Dev: `pytest`, `httpx`.  No new
dependency without adding it to `requirements.txt` / `requirements-dev.txt`
and verifying it installs and imports in the project's Python environment.
