# Contributing

Thanks for your interest in the multi-tank PLC/SCADA digital twin. This is
an educational engineering demonstrator, so contributions that improve the
*engineering fidelity* of the simulation are especially welcome.

## Engineering-first rules

- The Python backend is the single source of truth for plant state. Do not
  add a frontend-only simulation or a browser-side value that contradicts
  `scada/runtime.py`.
- The PLC stays authoritative. Commands must route through the PLC's
  validated setters or the Modbus write path — never mutate the output image
  (`aq` / `q`) directly.
- Keep the field interface dependency-free where it is today: the Modbus
  server and SQLite historian are stdlib-only by design.

## Setup

```bash
python -m venv .venv
# activate it, then:
pip install -e ".[dev]"
cd frontend && npm install && npm run build && cd ..
```

## Conventions

- **Git**: one commit per logical change — do not bundle unrelated work. See
  `AGENTS.md` for the full workflow.
- **Tags**: ISA-5.1 style (`TK-101`, `XV-101`, `LT-101`, `LIC-101`, `P-101`).
- **Units**: SI internally (m, m³/s, %, s); the SCADA layer formats L/s.
- **Tests**: run `pytest` and `python e2e_acceptance.py` before opening a PR.

## Pull requests

1. Open an issue describing the problem first.
2. Branch from `main`, keep changes small and focused.
3. Add or update tests for any behaviour change.
4. Update `README.md` / `docs/` when the public surface changes.
5. Run the full suite and note the results in the PR body.

This project is a simulation, not a certified industrial control system.
Do not add claims of safety compliance or production readiness.
