---
name: pin-deps
description: Pins this repo's unpinned requirements.txt to verified-working versions so a redeploy can't silently pull a breaking dependency. Use when hardening dependencies, when the user says "pin deps" / "pinna beroenden", or after a dependency-drift breakage.
---

Follow the pin-deps skill from skills/engineering/pin-deps/SKILL.md.

Quick steps:
1. In a working env: `python -m pytest tests/ -q` green + app boots (AppTest).
2. Capture versions: `importlib.metadata.version(...)` for each requirement.
3. Pin to prod-matching, known-good versions — flag major bumps, don't blind-bump.
4. Rewrite requirements.txt as `pkg==X.Y.Z` (same list/order).
5. Re-verify (`pytest -q` + boot) and ship with `/ship-pr`.
