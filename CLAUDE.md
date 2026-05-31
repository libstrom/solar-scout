# solar-scout

## Modell & konfiguration

Kör på **claude-opus-4-7** med adaptive thinking (konfigurerat i `.claude/settings.json`).
Dessa inställningar följer med repot — fungerar i lokala terminalen, cloud-sessioner och nya git-kloner.

## Kommandon

```bash
# Kör hela testsviten
python -m pytest tests/ -q

# Kör enbart acceptance-tester (appen är hel när dessa är gröna)
python -m pytest -m acceptance -v

# Syntaxkoll app.py / scanner.py
python -c "import ast; ast.parse(open('app.py').read()); print('OK')"

# Kör appen lokalt
streamlit run app.py
```

## Definition of done

**Appen är hel när `pytest -m acceptance` är grön.**

Acceptance-testerna i `tests/test_acceptance.py` täcker fem kritiska vägar:
1. **Login-väg** — inloggning lyckas / svenska felmeddelanden vid fel
2. **Scan-väg** — `scan_city` → `Lead` → `_lead_to_sb_row` → `load_leads`
3. **Budget-väg** — scan stoppas vid budgettak, partial leads bevaras
4. **DB-fel-väg** — Supabase-timeout → appen kraschar inte
5. **Cost-estimat-väg** — `estimate_scan_cost` blockerar oversized scans

En orkestrerare (se issue #56) kör `pytest -m acceptance` som grind innan den öppnar en PR.

## Agent skills

### Issue tracker

Issues live in GitHub Issues (`libstrom/solar-scout`). See `docs/agents/issue-tracker.md`.

### Triage labels

Default label strings (needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — `CONTEXT.md` at root + `docs/adr/`. See `docs/agents/domain.md`.

## claude-lens — veckouppdateringar

Håller dig uppdaterad om nya Claude/Anthropic-features, releases och skill-gaps.

```bash
# Kör digest (senaste 7 dagarna) → ~/claude-updates/YYYY-WW.md
python3 ~/.claude/scripts/claude-lens.py

# Printa till stdout
python3 ~/.claude/scripts/claude-lens.py --print

# Senaste 30 dagarna, snabbläge
python3 ~/.claude/scripts/claude-lens.py --since 30 --no-youtube
```

Eller använd slash-kommandot `/claude-lens` i valfri Claude Code-session.
Script: `~/.claude/scripts/claude-lens.py` · Skill: `~/.claude/skills/claude-lens/`

## Nyckelregler

- **Mapbox 24h-regel**: Mapbox-bilder får ALDRIG lagras — bara visas i UI
- **LM WMS**: Primär bildkälla (CC-BY, lagring tillåten)
- **Haiku pre-filter**: `_prefilter_building()` körs före Sonnet för att spara 60% kostnad
- **Glesbygd**: `scan_city()` kör ett fallback-pass på hela viewport för hus utanför OSM residential-polygoner
