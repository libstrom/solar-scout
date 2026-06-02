# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Modell & konfiguration

Kör på **claude-opus-4-8** (konfigurerat i `.claude/settings.json`). Inställningarna följer med repot.

## Kommandon

```bash
# Kör hela testsviten
python -m pytest tests/ -q

# Acceptance-tester — appen är hel när dessa är gröna
python -m pytest -m acceptance -v

# Kör ett enskilt test
python -m pytest tests/test_scan_cost.py::test_tracker_accumulates_cost -v

# Syntaxkoll
python -c "import ast; ast.parse(open('app.py').read()); print('OK')"
python -c "import ast; ast.parse(open('scanner.py').read()); print('OK')"

# Kör appen lokalt
streamlit run app.py
```

## Definition of done

**`pytest -m acceptance` grön = appen är hel.**

`tests/test_acceptance.py` täcker fem kritiska vägar:
1. **Login** — inloggning lyckas / svenska felmeddelanden vid fel
2. **Scan** — `scan_city` → `Lead` → `_lead_to_sb_row` → `load_leads`
3. **Budget** — scan stoppas vid budgettak, partial leads bevaras
4. **DB-fel** — Supabase-timeout → appen kraschar inte
5. **Kostnadsestimat** — `estimate_scan_cost` blockerar oversized scans

## Arkitektur

### Tre filer — ett system

| Fil | Roll |
|-----|------|
| `app.py` (~2600 rader) | Streamlit UI, auth, lead-hantering, kostnadsgate |
| `scanner.py` (~1900 rader) | OSM → byggnader → Claude Vision → Leads |
| `scan_cost.py` | Kostnadsestimat + runtime-budgetspärr |

`app.py` importerar `scanner.py` lazy (inne i funktioner, inte på modulnivå). `scanner.py` använder `BudgetTracker` från `scan_cost.py` via toppnivåimport.

### Scanner-pipeline: scan_city() steg för steg

```
1. Google Maps geocoding → viewport bbox
2. OSM solar-tags → gratisleads (instant)
3. _get_residential_areas() → Overpass → landuse=residential polygoner
4. Per zon: _get_osm_buildings() → filtrera bort icke-villor
5. scan_buildings_ai() med ThreadPoolExecutor(max_workers=4):
   └─ _fetch_satellite() → _enhance_contrast() (CLAHE 4×4) →
      _prefilter_building() (Haiku, 10 tokens) →
      _analyze_building() (Opus 4.8, cached few-shot) →
      Om UNSURE: Street View → andra analys
6. Glesbygd-pass: hela viewport utan landuse-filter (fångar 20-30% av villor)
7. Returnera (leads, ScanStats)
```

`on_progress`-callbacken i `app.py` sparar varje AI-lead progressivt till Supabase direkt — en krasch förlorar inga redan hittade leads.

### Auth-flöde

`init_auth()` läser i denna prioritetsordning:
1. `st.session_state["_auth_user"]` + `"access_token"` (snabbast)
2. `st.context.cookies` (HTTP request-headers, Streamlit 1.37+, synkront på första render)
3. `stx.CookieManager` (JS-baserad fallback för sessioner satta med äldre appversion)
4. `None` → ej inloggad

`get_supabase()` returnerar den autentiserade Supabase-klienten ur `st.session_state["_sb_user_client"]`. **Kalla aldrig `create_client()` direkt — det ger en anonym klient utan row-level security.**

`CookieManager` instansieras en gång och sparas i `st.session_state["_cookie_manager"]`. API: `.set()`, `.get()`, `.delete()` — **inte** `.remove()` (gammal API som kraschar).

### Bildkällor (fallback-ordning)

```
_fetch_satellite()
  1. LM minkarta WMS (gratis, ingen nyckel, 0.16m/px, lagringsbar)  ← primär
  2. LM official WMS (kräver LM_KEY, samma 0.16m/px, bara om formell licens behövs)
  3. Google Static Maps (~$0.002/req, lagringsbar)  ← circuit breaker: _google_exhausted
  4. Mapbox (ALDRIG lagra — 24h-regel, bara visa i UI)
```

`_google_exhausted = threading.Event()` sätts när Google returnerar 402/403/429. Nollställs av `reset_image_source_breakers()` i varje ny scan. **Mapbox-bilder får aldrig hamna i `image_url`-kolumnen i Supabase.**

`lm_wms_url(lat, lng)` genererar en publik, lagringsbar LM-URL utan nyckel — används för tooltip-bilder i kartan och som `image_url` på leads.

### Kostnadsmodell

```python
# USD per 1M tokens
OPUS_4_8   = ModelPricing(input=5.00, output=25.00, cache_write=6.25, cache_read=0.50)
SONNET_4_6 = ModelPricing(input=3.00, output=15.00, cache_write=3.75, cache_read=0.30)
HAIKU_4_5  = ModelPricing(input=1.00, output=5.00,  cache_write=1.25, cache_read=0.10)
USD_TO_SEK = 10.50
```

`estimate_scan_cost(n)` räknar på **värsta-fall** (utan Haiku-prefilter). Verklig kostnad är ~2.5× lägre tack vare att Haiku filtrerar 60% av byggnader. Siffran som visas i UI är konservativ med avsikt.

Gates: **CONFIRM_THRESHOLD_SEK = 200 kr** (checkbox krävs) · **DEFAULT_BUDGET_SEK = 5000 kr** (hård stop).

`BudgetTracker` är trådsäker (Lock). `budget.check()` kastas i main-tråden efter varje byggnad — inte inne i worker-trådar.

### Felklassificering som måste stämma

`APIQuotaExceededError` kastas av:
- Anthropic HTTP 429, 401 och **400 med "credit balance" i body** (HTTP 400 är Anthropics svar vid tomt saldo — INTE 402)
- Google Static Maps HTTP 429 och 403
- Google Street View HTTP 429 och 403

Om Anthropic returnerar en annan HTTP 400 (t.ex. ogiltig bild) → swallas tyst, returnerar `(False, False, False, "")`.

### Supabase-schema (scout_leads)

Kolumner som koden skriver/läser: `id`, `user_id`, `lat`, `lng`, `address`, `confidence`, `source` (osm|ai), `scan_source`, `solar_location`, `samtomt_solar_extra`, `status`, `david_note`, `user_confirmed`, `false_positive`, `reject_reason`, `tile_key`, `image_url`, `confirmed_image_url`, `building_type`, `ai_reasoning`, `needs_review`, `created_at`.

**`confidence`-kolumnen existerar inte** i databasen — ta inte med den i `.select()`-anrop.

### Streamlit-gotchas

- `st.rerun()` inne i `init_auth()` är förbjudet — dödar pågående formulär-submits.
- Widgets som skapas i loopar behöver explicit `key=` — auto-nyckeln krockar när containern återskapas under en pågående scan (`_render_live_leads` i `on_progress`).
- Scan-fel sparas i `st.session_state["_scanner_last_error"]` och visas vid nästa render — hantera inte fel med bara `st.error()` + `return` (syns inte om sidan renderas om).
- `st.components.v1.html` är borttaget sedan 2026-06-01 — använd `st.iframe(src="data:text/html;...", height=0)` för JS-injektion.

## Aktörer och geografisk scope

**David** (fältsäljare): ser bara Leads-fliken. Workflow: `ej_kontaktad → kontaktad → mote_bokat → kund/ej_intresserad`. `mote_bokat` triggar automatiskt mail till Linus via Resend API.

**Linus** (admin): kör scans, granskar UNSURE-leads i Granska-fliken.

**Primärt scanområde:** Nässjö, Eksjö, Vetlanda, Jönköping (Småland/SE3). Undvik Stockholm/Göteborg (timeout-risk) och ren glesbygd (för få OSM-byggnader).

## Nyckelregler

- **Mapbox 24h-regel**: Mapbox-bilder får ALDRIG lagras — bara visas i UI
- **LM WMS / LM minkarta**: lagringsbar (CC-BY), alltid OK att spara `image_url`
- **Haiku pre-filter**: `_prefilter_building()` körs före Opus för att spara ~60% kostnad
- **Glesbygd-pass**: `scan_city()` kör ett extra pass på hela viewport för hus utanför OSM residential-polygoner
- **OSM-attribution**: CSV-export måste innehålla attribution-header (ODbL); se `docs/adr/0001-osm-odbl-csv.md`

## Agent skills

- Issues: GitHub Issues (`libstrom/solar-scout`) — se `docs/agents/issue-tracker.md`
- Triage-labels: `docs/agents/triage-labels.md`
- Domändokumentation: `CONTEXT.md` + `docs/adr/`

## Hemligheter som krävs

| Variabel | Syfte |
|----------|-------|
| `SUPABASE_URL` + `SUPABASE_ANON_KEY` | DB & auth |
| `ANTHROPIC_API_KEY` eller `SOLAR_SCOUT_ANTHROPIC_KEY` | Claude Vision |
| `GOOGLE_API_KEY` | Geocoding (obligatorisk) + Static Maps (fallback) |
| `LANTMATERIET_KEY` | `consumer_key:consumer_secret` (valfri, faller tillbaka på Google) |
| `MAPBOX_TOKEN` | Kartvy i UI (valfri) |
| `STRIPE_SECRET_KEY` + `STRIPE_PRICE_*` | Betalning |
| `RESEND_API_KEY` | Mail vid mötesbokningar + kvotalarm |
