# Solar Scout

Streamlit-app för att hitta villor utan solceller i ett geografiskt område — underlag för fältförsäljning av solcellsinstallationer.

## Vad appen gör

1. **Hämtar byggnader** från OpenStreetMap (Overpass API) inom en stad eller ritat område
2. **Pre-filtrerar** varje byggnad med Claude Haiku (billigt, snabbt) — filtrerar bort ~60% som uppenbart inte är villor
3. **Analyserar taket** med Claude Sonnet/Opus via satellitbild — detekterar befintliga solceller
4. **Sparar leads** till Supabase i realtid — adress, koordinater, takets bild
5. **Visar leads** på interaktiv karta med mouseover-förhandsvisning av taket

## Köra lokalt

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Miljövariabler som krävs

| Variabel | Vad |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API — bildanalys |
| `SUPABASE_URL` | Projektets URL |
| `SUPABASE_KEY` | Service role-nyckel (anon fungerar ej med RLS) |
| `GOOGLE_API_KEY` | Google Static Maps (fallback-bildkälla) |
| `MAPBOX_TOKEN` | Kartvisning i UI |
| `RESEND_API_KEY` | E-postlarm |

Skapa `.env` i rotkatalogen eller sätt variablerna i Streamlit Cloud Secrets.

## Tester

```bash
# Hela sviten
python -m pytest tests/ -q

# Enbart acceptance (appen är hel när dessa är gröna)
python -m pytest -m acceptance -v
```

## Arkitektur

Se [CLAUDE.md](CLAUDE.md) för fullständig arkitektur, scanner-pipeline och kostnadskalkyl.
