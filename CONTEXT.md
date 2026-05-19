# solar-scout — domänkontext

## Produkt i ett mening

Streamlit-app som skannar svenska villakvarter med Claude Vision och
Lantmäteriets ortofoto för att hitta solcellstak och exportera
säljleads som CSV.

## Aktörer

| Roll | Namn | Ansvar |
|------|------|--------|
| Fältsäljare | Ivan | Besöker leads, utgår från Nässjö |
| Produktägare | Linus Bergström | Konfiguration, export, fakturering |

## Geografisk scope

**Hemort:** Nässjö (57.65°N, 14.70°E), Jönköpings län, Småland.

**Primärt scanområde:** orter inom ~2h bilkörning från Nässjö:

| Ort | Avstånd | Prioritet |
|-----|---------|-----------|
| Nässjö | 0 min | ★★★ |
| Eksjö | ~15 min | ★★★ |
| Vetlanda | ~30 min | ★★★ |
| Jönköping | ~35 min | ★★★ |
| Värnamo | ~50 min | ★★ |
| Tranås | ~25 min | ★★ |
| Sävsjö | ~25 min | ★★ |
| Växjö | ~60 min | ★★ |
| Borås | ~80 min | ★ |
| Linköping | ~90 min | ★ |
| Kalmar | ~90 min | ★ |

**SE3/SE4** är Sveriges elnätsområden (prisområdesplikter), inte städer.
Nässjö och hela Småland ligger i SE3. Malmö/Skåne är SE4.

## Domäntermer

| Term | Betydelse |
|------|-----------|
| Lead | Adress med troliga solceller — redo för säljbesök |
| SOLAR=YES | Claude är säker på solceller — visas direkt |
| SOLAR=UNSURE | Claude är osäker — hamnar i granskningskön |
| needs_review | Lead i gransk­ningskön (UNSURE eller OSM-tagg utan AI) |
| samtomt | Solceller på tomt men inte på hustaket (garage, carport) |
| solfångare | Solvärme­kollektor — INTE solceller, ska filtreras bort |
| eternite | Grå fibercementskiffer­plåt — vanlig på äldre svenska hus, falskpositivt |
| few-shot | De 3 verifierade Malmö-adresser som skickas som exempel till Claude |

## Detektionspipeline

```
OSM Overpass → byggnader inom bbox
     │
     ▼
LM WMS (minkarta.lantmateriet.se)   ← primär bildkälla (gratis, CC-BY)
     │                               Google Static Maps ← fallback
     ▼
_enhance_contrast()                 ← CLAHE 4×4-rutnät, YCbCr Y-kanal
     │
     ▼
_analyze_building()                 ← Claude Sonnet-4-6 vision
     │   few-shot: 3 Malmö-adresser (2×YES, 1×NO)
     │   prompt: smoothness-contrast, Nordic deny-list
     ▼
Lead (SOLAR=YES) → spara direkt i Supabase (progressiv)
Lead (SOLAR=UNSURE) → needs_review=True → granskningskö
```

## Few-shot exempel (verifierade)

| Koord | Etikett | Adress | Elvärde |
|-------|---------|--------|---------|
| 55.5706, 13.0379 | solar_yes | Risholmsgatan 8, Malmö | SE4 |
| 55.5751, 13.0708 | solar_yes_2 | Skimmelgatan 22, Malmö | SE4 |
| 55.5765, 13.0743 | solar_no | Remontgatan 41, Malmö | SE4 |
| 57.6398, 14.7056 | solar_yes_3 | Queckfeldtsgatan 17, Nässjö | SE3 |
| 57.6475, 14.7094 | solar_yes_4 | Stjärngatan 4, Nässjö | SE3 |
| 57.6530, 14.7126 | solar_yes_5 | Norrhagagatan 14, Nässjö | SE3 |
| 57.6349, 14.7104 | solar_no_3 | Smålandsgatan 48, Nässjö | SE3 |
| koordinater saknas | solar_yes_6 | Upplandsgatan 3, Nässjö | SE3 |

## Bildkällor — regler

| Källa | Lagring tillåten | Kommentar |
|-------|-----------------|-----------|
| LM WMS (minkarta.lantmateriet.se) | Ja | CC-BY, lagras i Supabase |
| Mapbox | **NEJ** | 24h-regel — får bara visas i UI, aldrig lagras |
| Google Static Maps | Ja | Fallback om LM misslyckas |

## Nyckelfiler

| Fil | Ansvar |
|-----|--------|
| `scanner.py` | Hela scanpipelinen: OSM, LM WMS, Claude vision, few-shot |
| `app.py` | Streamlit UI: login, scan-sida, granskningskö, CSV-export |
| `tests/` | 88 tester (73 unit + 15 F1-harness, 3 kräver riktig API-nyckel) |
| `docs/adr/` | Arkitekturbeslut (OSM-licens m.fl.) |
