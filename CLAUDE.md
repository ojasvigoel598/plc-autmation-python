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
  config.py                  # ALL engineering constants (SI units) + 3D layout
  process.py                 # plant model: tanks, valves, pump, transmitters, RK4
  pid.py                     # PID controller (anti-windup, bumpless, deriv-on-measurement)
  plc.py                     # IEC 61131-3 FBs, alarm manager, PLC scan engine
  leakdetect.py              # mass-balance leak detector (tanks only)
  leaks.py                   # LeakEvent + file-backed persistent store (data/leaks.json)
  runtime.py                 # field bus: sensors -> PLC -> actuators -> plant
  server.py                  # FastAPI: REST control + WebSocket telemetry + /app mount
  static/                    # 2D P&ID HMI (vanilla, no build step) at /
frontend/                    # React + Three.js 3D digital twin (built -> /app)
  src/sim.js                 # WS client (reconnect) + REST command pathway
  src/PlantScene.jsx         # 3D plant driven ONLY by simulation state
  src/PlcView.jsx            # PLC internals: I/O image, FBs, interlocks, program order
  src/LeakView.jsx           # leak investigation: latest + last 30, /leaks routes
  src/App.jsx                # SCADA panels, controls, alarms, trends, faults, auto-demo
tests/                       # pytest: physics, PID, PLC, closed loop, API
docs/generate_demo_figure.py # regenerates docs/demo_response.png
main.py + legacy modules     # ORIGINAL matplotlib demo (superseded, keep intact)
```

The **3D twin is a pure visual consumer of plant state**: it reads the
`config`/`state`/`trends` WebSocket messages and sends commands only through
REST (`/api/control/*`, `/api/faults/*`).  Never add a second simulation in
JavaScript, and never let the frontend mutate plant state directly.

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

Leaks exist **only as tank leaks** (`plant.leaks[tag]`).  The mass-balance
detector flags an unexplained level drop (measured flow balance vs. actual
volume change) and records a `LeakEvent` in `data/leaks.json`.  Never draw a
pipe/pump/valve leak the simulation does not model.

## PID rules

Parallel ISA form `u = Kp e + Ki∫e + Kd de/dt`.  Required behaviours:
derivative on measurement (no setpoint kick), first-order derivative filter,
back-calculation anti-windup, output limits, bumpless manual↔auto transfer.
Changing Kp/Ki/Kd/SP/MV in the UI must change controller behaviour.

## Testing & verification (mandatory before finishing any change)

```bash
python -m pytest tests/ -q          # must pass (currently 64 tests)
python run_scada.py                 # server on http://127.0.0.1:8000
cd frontend && npm run build        # rebuild the 3D twin (served at /app)
```

Verify at minimum: REST endpoints validate bad input (422), WebSocket
telemetry streams, a setpoint change is tracked, a disturbance is rejected,
E-stop forces safe state, a trip latches `FAULTED` and reset recovers it.
Run the acceptance scenario twice from a clean start to catch
intermittent/race issues.

## Workflow rules (repo owner requires these)

**Commit granularly — never one big commit at the end of a feature.**

For every feature:

1. Break it into logical, independently reviewable steps (scaffolding,
   backend/API, data/config, individual components, telemetry integration,
   controls, alarms/interlocks, tests, documentation).
2. Implement ONLY the next step.
3. Run the relevant tests/checks and verify it works.
4. Fix failures and re-test.
5. Commit that step immediately (small, descriptive, conventional message).
6. Move to the next step; repeat until the feature is complete.
7. Push to GitHub before continuing when appropriate.

A 200+ line feature spanning multiple logical stages should normally
produce **several** commits — one per completed, verified stage — not a
single commit.  Do not batch unrelated implementation/testing/docs/frontend/
backend work into one commit, and do not invent empty commits just to raise
the count: every commit must be a real, reviewable change.

- **Push to GitHub** after committing:
  `git push origin main` (remote is ojasvigoel598/plc-autmation-python).
- **Security check before every commit**: no API keys, tokens, passwords,
  private URLs, `.env`, generated logs/plots (except `docs/demo_response.png`
  and the committed `frontend/dist/`), or `__pycache__`/venvs.  `.gitignore`
  already covers most of these.
- Never force-push unless explicitly told to.
- Keep the README in sync with the actual implementation.
- Before starting any change, inspect `git log` and `git status` so you
  don't duplicate or overwrite existing work.

## Frontend rules

- **Single source of truth.** The 3D scene maps `state` -> geometry; a tank's
  liquid height, a pipe's flow particles, a pump's impeller, and a valve's
  disc all derive from the WebSocket `state`.  No decorative/idle animations.
- **Commands never bypass the PLC.** All interactions go through `sendCommand`
  (REST); the PLC decides, and the UI renders `request` vs enforced `output`
  vs actual `eff`, plus `interlockReason(state)`.
- **Data-driven scene.** Equipment positions/topology come from `/api/plant_config`
  (`config.LAYOUT`), so the scene adapts if the plant changes.  Do not
  hard-code tag/position maps in JSX when the config already provides them.
- **Build before release.** `npm run build` must succeed (Vite) and the
  output is served by FastAPI at `/app` (asset base is `/app/`).
- The **PLC tab** and **auto-demo** render/send only live backend state and
  REST commands — never a second simulation or bypass of interlocks.
- **`frontend/dist/` is committed** so the twin runs without Node for end
  users; always rebuild and re-commit it after any `frontend/src` change.
- Frontend deps live in `frontend/package.json`; no CDN, no global three.js
  script tag.  `frontend/node_modules/` and `/tools/` (portable Node) are
  gitignored.

## Dependencies

Runtime (Python): `numpy`, `fastapi`, `uvicorn`, `pydantic`, `websockets`,
`matplotlib` (legacy modules only).  Dev: `pytest`, `httpx`.  Frontend:
`react`, `react-dom`, `three`, `@react-three/fiber`, `@react-three/drei`,
`vite`, `@vitejs/plugin-react`.  No new dependency without adding it to
`requirements.txt` / `requirements-dev.txt` / `frontend/package.json` and
verifying it installs and imports in the project's actual environment.
