# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Agent skills

### Issue tracker
Issues live in GitHub Issues (`libstrom/solar-scout`). See `docs/agents/issue-tracker.md`.

### Triage labels
Default label strings (needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix). See `docs/agents/triage-labels.md`.

### Domain docs
Single-context — `CONTEXT.md` at root + `docs/adr/`. See `docs/agents/domain.md`.

---

## Commands

### Run the app locally
```bash
streamlit run app.py
```

### Run tests
```bash
pip install -r requirements.txt
python -m pytest tests/ -q
```

### Run a single test file or test
```bash
python -m pytest tests/test_f1_baseline.py -q
python -m pytest tests/test_f1_baseline.py -k "test_mock_f1" -q
```

Tests are dual-mode: without `ANTHROPIC_API_KEY` they run against deterministic mocks (always fast, always passes). With `ANTHROPIC_API_KEY` set, 5 additional tests hit the real API and assert F1 ≥ 0.80.

### Important env var note
Use `SOLAR_SCOUT_ANTHROPIC_KEY` (not `ANTHROPIC_API_KEY`) in `.env`/Railway. The app reads both but prefers the prefixed name — this avoids Claude Code CLI silently switching from OAuth billing to API credits when you `source .env`.

---

## Architecture

### Two-file structure

**`scanner.py`** — pure detection pipeline, no Streamlit/Supabase dependencies. Can be imported and tested in isolation. Entry points:
- `scan_city(city_name, ...)` — geocodes a city name, splits into bbox grid, runs `scan_bbox`
- `scan_bbox(south, west, north, east, ...)` — runs OSM + AI scan over a bounding box
- `scan_area_osm(...)` — OSM-only fast pass (no AI)
- `scan_buildings_ai(buildings, ...)` — AI pass over a list of pre-fetched buildings
- `scan_nearby_buildings(lat, lng, ...)` — scan within N metres of a point (used for granntomt feature)

**`app.py`** — Streamlit UI, Supabase auth/storage, Stripe payments. Imports `scanner.py`. Page functions: `page_scanner`, `page_review`, `page_leads`, `page_account`, `page_paywall`, `page_privacy`.

### Detection pipeline (inside `scanner.py`)

```
scan_city / scan_bbox
  └─ scan_area_osm()          # OSM solar-tagged buildings (instant)
  └─ _get_osm_buildings()     # all building footprints via Overpass
       └─ _process_building() # per building:
            ├─ _fetch_satellite()   # LM WMS → Google fallback → Mapbox fallback
            ├─ _enhance_contrast()  # CLAHE on YCbCr Y-channel
            ├─ _analyze_building()  # Claude Vision → HOUSE=YES/NO, SOLAR=YES/NO/UNSURE
            │    └─ Street View second pass if UNSURE
            └─ _has_extra_solar_nearby()  # OSM check for samtomt solar
```

Overpass is rate-limited via `_OVERPASS_SEM = threading.Semaphore(2)` (max 2 concurrent calls). HTTP 429 → 60 s wait.

### Lead deduplication
`tile_key = "bld/{osm_id}"` — one key per OSM building. `_lead_to_sb_row()` in `app.py` **must** include `tile_key`; without it dedup silently breaks (discovered 2026-05-21).

### Secrets / config
`_secret(key)` in `app.py` reads `st.secrets[key]` first, then `os.environ[key]`. In Railway, set env vars in the dashboard. Locally, set env vars or use `.streamlit/secrets.toml`.

### Few-shot learning
`_load_dynamic_few_shot(user_id)` loads confirmed leads and false-positives from Supabase as extra Claude few-shot examples. Static baseline examples are in `scanner.py` (Malmö + Nässjö SE3 coords).

### Supabase tables
- `scout_leads` — all leads; key columns: `tile_key`, `status`, `david_note`, `reject_reason`, `needs_review`, `user_confirmed`, `false_positive`, `ai_reasoning`
- `profiles` — user profiles with `is_admin`, `credits_balance`, `subscription_status`
- `enspecta_installations` — known existing customers to skip during scan

### Migrations
SQL migrations are in `migrations/` and must be applied manually in the Supabase SQL Editor. They are not auto-run. Applied so far: `001_add_lead_status.sql`, `002_add_tile_key.sql`, `003_add_reject_reason.sql`.

### Deploy
Railway (`railway.toml`). Start command: `streamlit run app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true`. Builder: Nixpacks.
