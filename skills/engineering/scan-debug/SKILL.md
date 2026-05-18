# scan-debug

Diagnose why a solar-scout scan returns 0 leads. Runs the scan pipeline
stage-by-stage with a known Swedish bbox and shows exactly where it breaks.

## When to use

- David reports "0 funna" (zero leads found)
- scan_city or scan_bbox returns empty list
- Unclear whether issue is Overpass, AI key, building filter, or image fetch

## Pipeline stages (in order)

```
1. _overpass()           → Overpass API reachable?
2. scan_area_osm()       → Any OSM solar-tagged buildings?
3. _get_osm_buildings()  → Any villa-type buildings found?
4. _fetch_lm_wms()       → Lantmäteriet WMS returns image?
5. _analyze_building()   → Anthropic API key valid + returns result?
6. _has_extra_solar_nearby() → samtomt Overpass calls work?
```

## Diagnostic script

Run this to test stages 1–3 without API keys (free, ~10s):

```python
import sys
sys.path.insert(0, "/home/user/solar-scout")
from scanner import _overpass, scan_area_osm, _get_osm_buildings

# Known Swedish villa area: Huskvarna centrum
S, W, N, E = 57.780, 14.270, 57.800, 14.310

print("Stage 1 — Overpass reachable:")
els = _overpass(f"[out:json][timeout:30]; node(1); out;", timeout=30)
print("  OK" if els is not None else "  FAIL")

print("Stage 2 — OSM solar leads:")
osm = scan_area_osm(S, W, N, E)
print(f"  {len(osm)} leads")

print("Stage 3 — Buildings in bbox:")
blds = _get_osm_buildings(S, W, N, E)
print(f"  {len(blds)} buildings")
if blds:
    print(f"  Sample: {blds[0]}")
```

## Common fixes

| Symptom | Cause | Fix |
|---|---|---|
| Stage 1 fails | Overpass down or 429 | Wait, retry |
| Stage 2 = 0, Stage 3 > 0 | Area has no OSM solar tags | Normal — use AI mode |
| Stage 3 = 0 | Building filter too strict, or OSM data sparse | Try larger bbox, or `building=yes` filter |
| Stage 3 > 0, 0 leads | AI key missing or image fetch fails | Check ANTHROPIC_API_KEY on Railway |
| Image fetch fails | LM WMS down | Test `_fetch_lm_wms(57.79, 14.29)` directly |

## Checking Railway logs

Use the Railway dashboard or ask Claude to check via the MCP server
(needs valid RAILWAY_API_TOKEN in .claude/settings.local.json).

Look for lines matching: `[scanner]` — these are the structured logs
from `_log.info()` in scanner.py.

Key log lines to find:
- `Overpass returned N elements` — N=0 means Overpass found nothing
- `scan_city no residential areas` — fallback triggered
- `_fetch_satellite source=lm_wms` — LM WMS worked
- `_analyze_building result` — what Claude decided
