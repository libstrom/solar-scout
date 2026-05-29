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

## Nyckelregler

- **Mapbox 24h-regel**: Mapbox-bilder får ALDRIG lagras — bara visas i UI
- **LM WMS**: Primär bildkälla (CC-BY, lagring tillåten)
- **Haiku pre-filter**: `_prefilter_building()` körs före Sonnet för att spara 60% kostnad
- **Glesbygd**: `scan_city()` kör ett fallback-pass på hela viewport för hus utanför OSM residential-polygoner

## Karpathy-loopen ("kör karpathy")

När Linus säger **"kör karpathy"** menar han den självförbättrande loopen (à la
Andrej Karpathy): ändra detektions­prompten/few-shot → mät F1 → behåll om scoren
går upp, annars revert. Det är **inte** ett kommando eller en skill — leta inte efter det.

- **Eval-harness**: `tests/test_f1_baseline.py` — labelade adresser → precision/recall/F1, grind `F1 >= 0.80`.
- **Det som trimmas**: `_analyze_building`-prompten + few-shot i `scanner.py`.
- **Kör**: `ANTHROPIC_API_KEY=… python -m pytest tests/test_f1_baseline.py -q`. Utan nyckel skippas real-testet (mock ger alltid F1=1.0 → inget att optimera mot).
- **Krav**: `ANTHROPIC_API_KEY` i miljön + nät till api.anthropic.com och minkarta.lantmateriet.se. ~3 anrop per mätning (billigt).
- **Svaghet**: eval-setet är bara 3 Malmö-adresser (SE4); hemorten är SE3 (Nässjö). Bredda med SE3-grundsanning för meningsfull optimering.
