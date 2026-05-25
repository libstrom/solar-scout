# solar-scout — Handoff 2026-05-21 (session 2)

## Vad som gjordes denna session

### Levererat och pushat (PR #32 — draft, ej mergad)

| Commit | Vad |
|--------|-----|
| `6321855` | Inline ✅/❌ review direkt i scan-vyn |
| `924e458` | Real-time lead-display under scan (kort dyker upp medan scan körs) |
| `c3b9310` | Google / Hitta.se / Maps-knappar per lead i Leads-fliken |
| `d58e81b` | `tile_key` dedup fix — kolumn saknades i DB, dedup fungerade aldrig |
| `ab3f788` | Superpowers (obra) skills installerade lokalt i `.claude/commands/` |
| `a5132b4` | **Granntomt-feature** — se nedan |

### Granntomt-feature (senaste committen)

**Problem:** David ser solceller på en grannfastighet i satellit-bilden, men leadet pekar på fel tak.

**Lösning:**
- `page_review` i `app.py`: ❌-knappen har nu en selectbox — `Inga solceller / Granntomt / Solfångare / Eternite`
- Om orsak = "Granntomt": `scan_nearby_buildings()` körs automatiskt inom 60 m-radie
- Nya leads infogas direkt i Supabase och visas i Leads-fliken
- `scanner.py`: ny funktion `scan_nearby_buildings()` återanvänder `_get_osm_buildings` + `_process_building`
- `migrations/003_add_reject_reason.sql`: kolumn `reject_reason TEXT` redan applicerad i Supabase

### DB-migrationer (alla applicerade i Supabase)

| Fil | Kolumn | Status |
|-----|--------|--------|
| `migrations/001_add_lead_status.sql` | `status`, `david_note` | ✅ applicerad |
| `migrations/002_add_tile_key.sql` | `tile_key TEXT DEFAULT ''` | ✅ applicerad |
| `migrations/003_add_reject_reason.sql` | `reject_reason TEXT` | ✅ applicerad |

---

## Aktuellt tillstånd

- **Branch att jobba på:** `claude/fix-solar-panel-results-DZGVP`
- **Open PR:** #32 (draft) — granntomt-feature, redo att mergas
- **Main branch:** är bakom feature-branchen — PR #32 behöver mergas

### Kvarstående issues (GitHub)

| # | Titel | Prioritet |
|---|-------|-----------|
| #25 | Förbättra precision/recall för solcellsidentifiering | Hög |
| #26 | Retroaktiv few-shot — bekräftade leads förbättrar framtida scans | Hög |
| #29 | Review queue UX | Medium |
| #30 | Bulk-scan Nässjö | Medium |
| #15 | LANTMATERIET_KEY i host-miljön | Låg |
| #16 | Ladda upp satellitbild manuellt | Låg |

### Säkerhet — kräver manuell åtgärd av Linus

- **GitHub token läcktes tidigare** — måste roteras på GitHub (Settings → Developer settings → Personal access tokens)
- **`enspecta_installations`-tabell har RLS inaktiverat** i Supabase — aktivera RLS eller acceptera risken

---

## Affärskontext (viktigt för nästa session)

- **B2C** — privatpersoner, inte företag
- **David** är mötesbokare. Erbjuder **kostnadsfritt och förutsättningslöst platsbesök** för energirådgivning (VP, PV, BESS, EMS) — inget köpkrav
- **Linus** gör det faktiska besöket/rådgivningen
- **Primärflöde:** scan → adress → Google/Hitta-länk → David ringer → bokar möte → Linus besöker
- **NIX-telefon:** Gäller ej (kostnadsfri rådgivning, inte marknadsföring)
- **GDPR:** Kundadresser får inte ligga i publik kod

---

## Viktiga tekniska invarianter

- **Mapbox 24h-regel:** Mapbox-bilder FÅR INTE lagras. Bara Lantmäteriet (LM WMS) tillåts lagras.
- `tile_key = "bld/{osm_id}"` — dedup-nyckel per byggnad
- `_lead_to_sb_row()` i `app.py` — måste inkludera `tile_key`
- `ANTHROPIC_API_KEY` används i scanner, läses via `_secret()`
- Supabase project ref: `ozmpxldmgivggbmwhtjt`
- Superpowers skills ligger i `.claude/commands/` (obra/superpowers, inte Matt Pocock)

---

## Nästa steg (rekommenderat)

1. **Merga PR #32** till main och deploya
2. **Testa granntomt-featuren** live med David
3. **Issue #25** — förbättra precision: few-shot pipeline + SE3-tak-prompts → >80% på Nässjö-hustak
4. **Rotera GitHub-token** (säkerhetskritiskt — gör detta nu)
