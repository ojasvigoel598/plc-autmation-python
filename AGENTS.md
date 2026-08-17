# AGENTS.md

Agent instructions for this repository. See `CLAUDE.md` for the full
engineering conventions (SI units, scan-cycle discipline, leak model, etc.).

## Commit rule (non-negotiable)

**Commit and push after every single logical or incremental change — no
matter how small. Never wait to bundle updates into a major feature.**

Examples of changes that each get their **own** commit + push:

- one line of code (e.g. `import xx` or a typo fix)
- one config value change
- one CSS rule
- one README/CLAUDE.md/AGENTS.md sentence
- one test, one bug fix, one endpoint, one component

The workflow for every change is:

```text
make the change
  → run the relevant checks (tests / build / typecheck)
  → fix failures and re-test
  → security check (no secrets)
  → commit immediately with a clear message
  → push to GitHub
  → only then make the next change
```

Never finish a large feature first and commit it as one large commit. A
feature with many logical stages must produce many small, reviewable commits
— one per completed, verified stage.

Do **not** fabricate empty commits to inflate the count: every commit must be
a real, reviewable change.

Remote: `ojasvigoel598/plc-autmation-python` (branch `main`).
