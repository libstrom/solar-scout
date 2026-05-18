---
name: scan-debug
description: Diagnose why solar-scout scan returns 0 leads. Runs pipeline stage-by-stage to pinpoint the break. Use when David reports "0 funna" or scan returns empty.
---

Run the scan-debug skill from skills/engineering/scan-debug/SKILL.md.

Stage-by-stage diagnosis:
1. Test Overpass reachability
2. Count OSM solar leads in a known Swedish bbox
3. Count buildings found by _get_osm_buildings
4. Test LM WMS image fetch
5. Check if ANTHROPIC_API_KEY is set

Report which stage fails and what fix applies from the skill's table.
