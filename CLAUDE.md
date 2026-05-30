# solar-scout

## Modell & konfiguration

Kör på **claude-opus-4-7** med adaptive thinking (konfigurerat i `.claude/settings.json`).
Dessa inställningar följer med repot — fungerar i lokala terminalen, cloud-sessioner och nya git-kloner.

## Kommandon

```bash
# Kör tester
python -m pytest tests/ -q

# Syntaxkoll app.py / scanner.py
python -c "import ast; ast.parse(open('app.py').read()); print('OK')"

# Kör appen lokalt
streamlit run app.py
```

## Agent skills

### Issue tracker

Issues live in GitHub Issues (`libstrom/solar-scout`). See `docs/agents/issue-tracker.md`.

### Triage labels

Default label strings (needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — `CONTEXT.md` at root + `docs/adr/`. See `docs/agents/domain.md`.

## File Delivery

When delivering code files, write them directly to disk with the Write tool instead of pasting base64-encoded blocks in chat. Never use base64 paste delivery for file transfer.

GitHub CDN aggressively caches raw files; after pushing an updated parser, bypass the cache (use commit-pinned URLs or cache-busting query params) rather than re-fetching the same path.

## Deployment

Vercel deployment requires a token that is NOT available in the sandbox. Do not attempt automated Vercel deploys; provide the deploy command for the user to run locally instead.

## Parser / Pipeline

The XLSM/PDF parser uses the export name `extractXlsmFields` (not `extractXlsm`). Verify export names match imports before running the pipeline.

## Data Sources

The enspecta.tab register has 30 columns and 47 249 rows structured as follows:

- **Kolumner 0–23**: Huvudpost (CaseID, Status, datum, adress, postnr, ort, kommun, namn, fastighetsbeteckning, email, telefon, etc.)
- **Kolumner 24–28**: Intressent-kontakt (förnamn, efternamn, email, telefon1, telefon2)
- **Tomma col0-rader**: Intressent-rader kopplade till föregående CaseID via radposition

**Intressent-statistik (verifierat 2026-05-25):**
- 12 125 huvudposter har ≥1 intressent — varav 10 892 är Säljare-besiktningar
- 10 761 intressenter har email, 10 294 har telefon
- Kontaktdata FINNS — match.mjs extraherar den inte än

**Rätt prioritetsordning för match.mjs** (implementeras i enrichLeads.mjs):
1. Köpare (nuvarande ägare — bäst)
2. Intressent från Säljare-besiktning (trolig köpare — bra)
3. Säljare (har flyttat — sämst)

## Nyckelregler

- **Mapbox 24h-regel**: Mapbox-bilder får ALDRIG lagras — bara visas i UI
- **LM WMS**: Primär bildkälla (CC-BY, lagring tillåten)
- **Haiku pre-filter**: `_prefilter_building()` körs före Sonnet för att spara 60% kostnad
- **Glesbygd**: `scan_city()` kör ett fallback-pass på hela viewport för hus utanför OSM residential-polygoner
