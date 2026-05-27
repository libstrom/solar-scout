# pin-deps

Pin solar-scout's unpinned `requirements.txt` to verified-working versions so a
redeploy can't silently pull a breaking dependency. This is the root cause behind
drift-breakage (e.g. an unpinned Streamlit dropping a deprecated API on redeploy).

## Why

`requirements.txt` lists bare package names (no versions). Streamlit Cloud redeploys `main`
and reinstalls latest → "works today, breaks on next deploy". Pinning freezes a
known-good set.

## Steps

### 1. Capture what currently works
In an environment where the app boots and tests pass:
```bash
python -m pytest tests/ -q                  # must be green
python -c "from streamlit.testing.v1 import AppTest; print(AppTest.from_file('app.py').run().exception)"
python -c "import importlib.metadata as m; [print(f'{p}=={m.version(p)}') for p in open('requirements.txt').read().split()]"
```

### 2. Decide the pin set — do NOT blindly take latest
- Prefer the versions **production currently runs** (ask the user, or check the deploy).
- Flag major bumps (e.g. pandas 2.x → 3.x); those can regress paths you can't test.
- The cloud box has no API keys, so you can verify only boot + unit tests here —
  NOT the live scan, auth, or Stripe. Say so and let the human confirm major bumps.

### 3. Write the pins
Rewrite `requirements.txt` as `package==X.Y.Z`, keeping the same package list and order.

### 4. Verify, then ship
```bash
python -m pytest tests/ -q
```
Boot via AppTest again, then ship with `/ship-pr`.

## Gotchas (this repo / cloud box)
- `googlemaps` sdist build fails under Debian setuptools → install with
  `SETUPTOOLS_USE_DISTUTILS=stdlib`.
- `supabase` install trips on Debian-managed PyJWT → add `--ignore-installed PyJWT`.
