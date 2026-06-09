---
name: run-solar-scout
description: Build, run, and drive Solar Scout. Use when asked to start solar-scout, run its tests, build it, take a screenshot of its UI, or interact with the running app.
---

Solar Scout is a Streamlit web app (Python). Drive it via `python3 .claude/skills/run-solar-scout/smoke.py` — it starts the server, takes a screenshot via Playwright, and exits 0 on success. All paths below are relative to repo root.

## Prerequisites

```bash
# Python deps (googlemaps wheel is broken — install source manually)
pip install -r requirements.txt 2>/dev/null || true
pip download googlemaps==4.10.0 -d /tmp/gm_dl -q
tar xzf /tmp/gm_dl/googlemaps-4.10.0.tar.gz -C /tmp/gm_dl
cp -r /tmp/gm_dl/googlemaps-4.10.0/googlemaps /usr/local/lib/python3.11/dist-packages/
pip install streamlit supabase stripe pandas openpyxl anthropic streamlit-folium folium httpx pillow pytest extra-streamlit-components msoffcrypto-tool -q

# Playwright browser
pip install playwright -q
python3 -m playwright install chromium
```

Runtime: Python 3.11. No node/npm needed.

## Setup

No build step. Env vars are loaded from Streamlit secrets (Streamlit Cloud) or OS env:

```bash
export SUPABASE_URL=...        # required for login
export SUPABASE_ANON_KEY=...   # required for login
export GOOGLE_API_KEY=...      # required for geocoding
export ANTHROPIC_API_KEY=...   # required for AI scan
# Optional:
export LANTMATERIET_KEY=...    # consumer_key:consumer_secret — free LM WMS still works without this
export MAPBOX_TOKEN=...        # map UI only
```

Without `SUPABASE_URL`/`SUPABASE_ANON_KEY` the app starts but shows a warning and login fails. The smoke driver still passes — it only checks that the page renders.

## Run (agent path)

```bash
python3 .claude/skills/run-solar-scout/smoke.py
```

Screenshots → `/tmp/shots/solar-scout-login.png`. Logs → `/tmp/streamlit.log`.

To keep the server running after the smoke (for interactive use):

```bash
python3 .claude/skills/run-solar-scout/smoke.py --keep --port 8501
# server stays up at http://localhost:8501
# screenshot taken as smoke confirmation
```

To screenshot a specific page interactively, after `--keep`:

```python
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(args=["--no-sandbox"])
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto("http://localhost:8501")
    page.wait_for_selector('[data-testid="stApp"]', timeout=12000)
    page.wait_for_timeout(2000)
    page.screenshot(path="/tmp/shots/current.png")
    browser.close()
```

## Run (human path)

```bash
streamlit run app.py   # → browser opens http://localhost:8501. Ctrl-C to stop.
```

## Test

```bash
python3 -m pytest tests/ -q                     # full suite
python3 -m pytest -m acceptance -v              # critical paths only (5 tests)
python3 -m pytest tests/test_scan_cost.py -v    # cost model unit tests
```

## Gotchas

- **`pkill -f streamlit` kills the shell** — the bash eval string contains "streamlit", so `pkill -f streamlit` matches and kills the calling process (exit 144). Use `fuser -k 8501/tcp` to kill by port instead.
- **`googlemaps` wheel build fails** with `AttributeError: install_layout` on Ubuntu's bundled setuptools. Workaround: copy the source package directly to site-packages (see Prerequisites above). The smoke.py itself doesn't need googlemaps at import time.
- **Blank first screenshot** — Streamlit serves an HTML shell immediately but the React app takes 2–3 s to hydrate. Always `wait_for_selector('[data-testid="stApp"]')` + `wait_for_timeout(2000)` before `screenshot()`.
- **Port in use / `EADDRINUSE`** — use `fuser -k 8501/tcp` before restarting, not pkill.
- **No env vars → warning banner** — the "SUPABASE_URL saknas" warning is expected when running locally without secrets. The page still renders and smoke passes.
- **`nohup` required for subprocess launch** — `subprocess.Popen(["streamlit", ...])` without nohup gets SIGSTKFLT (exit 144) in this container environment. The smoke driver uses `nohup python3 -m streamlit run`.

## Troubleshooting

- **Exit 144 from any bash command**: you have "streamlit" or another keyword in the eval string that pkill/kill matched. Use `fuser -k <port>/tcp` instead.
- **Streamlit logs blank after start**: check `/tmp/streamlit.log` — common cause is missing `app.py` in cwd or wrong Python path.
- **`playwright._impl._api_types.Error: Executable doesn't exist`**: run `python3 -m playwright install chromium` first.
