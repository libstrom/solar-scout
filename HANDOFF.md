# solar-scout — Handoff 2026-05-21

Överlämning till nästa agent/session. Allt är committadt och pushat till `main`.

---

## Var appen befinner sig just nu

**Appen är INTE live** — Railway-trial gick ut. Koden är klar för deploy.

### Två manuella steg återstår (Linus gör dem, tar 5 min)

1. **Gör GitHub-repot publikt**
   → github.com/libstrom/solar-scout/settings → Danger Zone → Change visibility → Public

2. **Deploya på Streamlit Community Cloud (gratis)**
   → share.streamlit.io → New app → `libstrom/solar-scout` → `app.py`
   → Lägg till secrets (se nedan)

### Secrets att lägga in i Streamlit Community Cloud

```toml
SUPABASE_URL        = "https://ozmpxldmgivggbmwhtjt.supabase.co"
SUPABASE_ANON_KEY   = "<hämta från supabase.com/dashboard/project/ozmpxldmgivggbmwhtjt/settings/api>"
ANTHROPIC_API_KEY   = "<Anthropic API-nyckel>"
MAPBOX_TOKEN        = "<Mapbox public token>"
GOOGLE_API_KEY      = "<Google Maps API-nyckel>"
```

---

## Supabase — projekt och tabeller

**Projekt:** SalesPilot (`ozmpxldmgivggbmwhtjt`, eu-north-1)
Solar-scout delar projekt med SalesPilot av kostnadsskäl (separat projekt kostar $10/mån extra).

### Tabeller

| Tabell | Rader | Syfte |
|--------|-------|-------|
| `scout_leads` | 76 | AI-hittade solcellsleads |
| `enspecta_installations` | 45 | Befintliga kundinstallationer — skippas vid scan |
| `profiles` | 1 | Linus Bergström (linus.bergstrom@enspectaenergi.se) |

### Viktiga kolumner i scout_leads

| Kolumn | Typ | Betydelse |
|--------|-----|-----------|
| `needs_review` | bool | SOLAR=UNSURE → visas i granskningskön |
| `user_confirmed` | bool | David/Linus har bekräftat solceller |
| `false_positive` | bool | Markerad som FEL av användare |
| `confirmed_image_url` | text | URL till bild i Supabase Storage (`lead-images`-bucket) |

**OBS:** Auth-användare kan INTE SQL-migreras. Linus måste registrera nytt lösenord om ny Supabase-instans skapas.

---

## Vad som byggdes i denna session

### Funktioner (alla mergade till main)

| Feature | Fil | Commit |
|---------|-----|--------|
| Dynamisk few-shot från Supabase | `scanner.py` | f97c1dd |
| Progress-mätare med adress + leads i realtid | `app.py` | f97c1dd |
| Tinder-vy i granskningskön | `app.py` | f97c1dd |
| Rätt koordinat Queckfeldtsgatan 17 (var 600m fel) | `scanner.py` | cd5d695 |
| Supabase MCP-server i projektinställningar | `.claude/settings.json` | e379eec |
| Enspecta kundadresser → Supabase tabell | `supabase/migrations/` | 58e184b |
| false_positive + confirmed_image_url kolumner | `supabase/migrations/` | 9cb7671 |
| Cookie-persistens 30 dagar (David loggas inte ut) | `app.py` | 560a06c |
| Prompt caching (~70% billigare Claude-anrop) | `scanner.py` | 560a06c |
| Street View second pass för SOLAR=UNSURE | `scanner.py` | 560a06c |
| SE3-negativ few-shot (Smålandsgatan 48, Nässjö) | `scanner.py` | 560a06c |
| Security: Mapbox-token borttagen ur test_scanner.py | `test_scanner.py` | e1829f9 |
| GDPR: known_installations.py borttagen ur historik | — | git-filter-repo |

### Säkerhet / GDPR

- `known_installations.py` (45 kundadresser) raderad **ur hela git-historiken** via git-filter-repo
- `supabase/migrations/20260520_enspecta_installations.sql` raderad ur historiken
- Mapbox public token borttagen ur koden
- Repo är nu GDPR-rent och redo att göras publikt

---

## Detektionspipeline — aktuellt tillstånd

```
OSM Overpass → byggnader inom bbox
     │
     ▼
_is_existing_customer()    ← skippar byggnader inom 30m av Enspecta-kund
     │                        (laddar från enspecta_installations i Supabase)
     ▼
LM WMS (gratis, CC-BY)     ← primär bildkälla
     │   Google Maps        ← fallback
     ▼
_enhance_contrast()        ← CLAHE 4×4-rutnät, YCbCr Y-kanal
     │
     ▼
_analyze_building()        ← Claude claude-sonnet-4-6 vision
     │   few-shot: 4 hårdkodade (2×SE4, 2×SE3) + dynamiska från Supabase
     │   cache_control: ephemeral på system + sista few-shot bild
     │   SOLAR=UNSURE → Street View second pass
     ▼
SOLAR=YES  → spara direkt i scout_leads (progressiv)
SOLAR=UNSURE → needs_review=True → granskningskö
SOLAR=NO   → kasseras
```

### Dynamisk few-shot (självförbättring)

`_load_dynamic_few_shot(user_id)` hämtar bekräftade leads (`user_confirmed=True`) och false positives (`false_positive=True`) med `confirmed_image_url` från Supabase och lägger dem till few-shot-listan. Dvs: varje gång David bekräftar eller avvisar ett lead i granskningskön lär sig AI:n av det vid nästa scan.

---

## Few-shot koordinater (verifierade)

| Koord | Etikett | Adress | Status |
|-------|---------|--------|--------|
| 55.5706, 13.0379 | solar_yes | Risholmsgatan 8, Malmö | ✓ verifierad |
| 55.5765, 13.0743 | solar_no | Remontgatan 41, Malmö | ✓ verifierad |
| 57.64119, 14.70581 | solar_yes_3 | Queckfeldtsgatan 17, Nässjö | ✓ korrigerad 2026-05-21 |
| 57.6349, 14.7104 | solar_no_3 | Smålandsgatan 48, Nässjö | ✓ verifierad |

**OBS:** Upplandsgatan 3, Nässjö (solar_yes_6) saknar fortfarande koordinater.

---

## Bildkällor — hårda regler

| Källa | Lagring tillåten | Kommentar |
|-------|-----------------|-----------|
| LM WMS (minkarta.lantmateriet.se) | **JA** | CC-BY, lagras i Supabase Storage |
| Mapbox | **NEJ** | 24h-regel — visas bara i UI, lagras aldrig |
| Google Static Maps | Ja | Fallback |

---

## Nästa prioriteringar

1. **Deploy** (Linus, 5 min) — se steg ovan
2. **Enspecta-branding** — logga + färger i Streamlit (`app.py`, custom CSS)
3. **Upplandsgatan 3 koordinater** — WGS84-koordinat för solar_yes_6
4. **YOLOv8 pre-filter** — testades, fungerar EJ på svenska LM-ortofoton (tränad på andra bildtyper). Avvakta.
5. **Eget Supabase-projekt** — $10/mån extra, låg prioritet tills volym växer

---

## Nyckelfiler

| Fil | Ansvar |
|-----|--------|
| `scanner.py` | Hela scanpipelinen, few-shot, dynamisk inlärning |
| `app.py` | Streamlit UI: login, scan, granskning, leads, export |
| `tests/` | 95 tester (92 unit + 3 kräver riktig API-nyckel) |
| `.claude/settings.json` | MCP-servers: Supabase + Railway |
| `CONTEXT.md` | Domäntermer och arkitektur |
| `docs/adr/` | Arkitekturbeslut |

---

## Aktörer

| Roll | Namn | Kontakt |
|------|------|---------|
| Fältsäljare | David | Nässjö, kör appen dagligen |
| Produktägare | Linus Bergström | linus.bergstrom@enspectaenergi.se |
