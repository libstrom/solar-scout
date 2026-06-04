# solar-scout — domänkontext

## Produkt i ett mening

Streamlit-app som skannar svenska villakvarter med Claude Vision och
Lantmäteriets ortofoto för att hitta solcellstak och exporterar
säljleads som Excel-fil.

## Två lead-pipeline (ADR-beslut 2026-06-04)

Solar-scout har två parallella leadkällor som aldrig möts i koden just nu:

| Pipeline | Hittar | Pitch | Output |
|----------|--------|-------|--------|
| Visual scanner (app.py + scanner.py) | Fastigheter med sol (batteri-kandidater) | "Du har redan sol, lägg till batteri" | Supabase, Davids UI i appen |
| Energideklarationer (makeLeads.py) | Fastigheter utan sol men med hög förbrukning/låg energiklass | "Installera sol, halvera elräkningen" | leads.xlsx, Excel-ringlista |

Beslut: acceptera separata flöden kortsiktigt. På sikt importeras energi-leads
till Supabase så David ser allt i ett ställe, men det är ett separat projekt.

## Aktörer

| Roll | Namn | Ansvar |
|------|------|--------|
| Fältsäljare | David | Besöker leads, sätter status, bokar möten. Ser bara **Leads-fliken**. |
| Produktägare | Linus Bergström | Kör scans, granskar AI-leads, får mail vid mötesbokningar |

## Davids arbetsflöde (viktigast att hålla enkelt)

1. Loggar in → ser Leads-fliken direkt (första fliken)
2. Ser bekräftade leads med adress + satellit-länk
3. Sätter status: `ej_kontaktad → kontaktad → mote_bokat → ej_intresserad → kund`
4. Skriver notering per lead ("inte hemma, prova tisdag kl 17")
5. När status = `mote_bokat` → Resend API skickar mail till Linus automatiskt
6. Laddar ner Excel-fil (`.xlsx`, auto-justerade kolumner)

## Lead-statuspipeline

```
ej_kontaktad → kontaktad → mote_bokat ──→ [mail till Linus]
                        ↘ ej_intresserad
                        ↘ kund
```

Kolumner i `scout_leads`: `status TEXT DEFAULT 'ej_kontaktad'`, `david_note TEXT`
Migration: `migrations/001_add_lead_status.sql` (redan körd 2026-05-21)

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
| few-shot | Verifierade exempel som skickas till Claude för kalibrering |
| mote_bokat | Status som triggar automatiskt mail till Linus |

## Detektionspipeline

```
OSM Overpass → byggnader inom bbox   ← max 2 samtidiga anrop (_OVERPASS_SEM)
     │
     ▼
LM WMS (minkarta.lantmateriet.se)   ← primär bildkälla (gratis, CC-BY)
     │                               Google Static Maps ← fallback
     ▼
_enhance_contrast()                 ← CLAHE 4×4-rutnät, YCbCr Y-kanal
     │
     ▼
_analyze_building()                 ← Claude Sonnet-4-6 vision
     │   few-shot: SE3-kalibrerade exempel (Nässjö)
     │   prompt: smoothness-contrast, Nordic deny-list (Skandiategel, eternite, kopparplåt)
     ▼
Lead (SOLAR=YES) → spara direkt i Supabase (progressiv)
Lead (SOLAR=UNSURE) → needs_review=True → granskningskö
```

## Känd bugg och fix: 0 leads på andra konsekutiva scan

**Orsak:** 4 parallella workers × 2 Overpass-anrop vardera = upp till 8 samtida
anrop → overpass-api.de rate-limiterar (HTTP 429) → `_overpass()` returnerar `[]`
→ 0 byggnader → 0 leads.

**Fix (scanner.py):** `_OVERPASS_SEM = threading.Semaphore(2)` begränsar till
max 2 samtida Overpass-anrop. HTTP 429 ger 60 s väntan. Backoff: 5/20/60 s.

**Symptom:** Scan av stad B direkt efter stad A returnerar 0 leads.
**Lösning för David:** Vänta 2-3 min mellan scans om det händer.

## Few-shot exempel (verifierade)

| Koord | Etikett | Adress | Elvärde |
|-------|---------|--------|---------|
| 55.5706, 13.0379 | solar_yes | Risholmsgatan 8, Malmö | SE4 |
| 55.5765, 13.0743 | solar_no | Remontgatan 41, Malmö | SE4 |
| 57.64119, 14.70581 | solar_yes_3 | Queckfeldtsgatan 17, Nässjö | SE3 |
| 57.6349, 14.7104 | solar_no_3 | Smålandsgatan 48, Nässjö | SE3 |

## Bildkällor — regler

| Källa | Lagring tillåten | Kommentar |
|-------|-----------------|-----------|
| LM WMS (minkarta.lantmateriet.se) | Ja | CC-BY, lagras i Supabase |
| Mapbox | **NEJ** | 24h-regel — får bara visas i UI, aldrig lagras |
| Google Static Maps | Ja | Fallback om LM misslyckas |

## Detektionspipeline — dynamisk few-shot

`_load_dynamic_few_shot(user_id)` laddar bekräftade leads och false positives
från Supabase som extra few-shot-exempel. AI:n lär sig automatiskt av varje
lead David bekräftar eller avvisar i granskningskön.

## Externa tjänster

| Tjänst | Secret-nyckel | Syfte |
|--------|--------------|-------|
| Supabase | SUPABASE_URL, SUPABASE_ANON_KEY | Databas + auth |
| Anthropic | ANTHROPIC_API_KEY | Claude Vision |
| Google Maps | GOOGLE_API_KEY | Geocoding + fallback-bilder |
| Mapbox | MAPBOX_TOKEN | Karta i UI (ej lagring) |
| Resend | RESEND_API_KEY | Mail till Linus vid mötesbokningar |
| Stripe | STRIPE_SECRET_KEY | Betalningar (credits) |
| GitHub | GITHUB_TOKEN | Auto-rapportera crashes som Issues |

## Nyckelfiler

| Fil | Ansvar |
|-----|--------|
| `scanner.py` | Hela scanpipelinen: OSM, LM WMS, Claude vision, few-shot |
| `app.py` | Streamlit UI: login, scan, granskningskö, leads, Excel-export |
| `migrations/` | SQL-migreringar för Supabase (kör manuellt i SQL Editor) |
| `tests/` | 101 tester (98 unit + 3 kräver riktig API-nyckel) |
| `docs/adr/` | Arkitekturbeslut (OSM-licens m.fl.) |

## Öppna issues (GitHub)

| # | Titel | Status |
|---|-------|--------|
| 25 | Mät precision/recall per scan | ready-for-agent |
| 26 | Retroaktiv few-shot bootstrap | ready-for-agent |
| 29 | Granskningskö UX — tangentbord + konfidenspoäng | ready-for-agent |
| 30 | Bulk-scan Nässjö kommun | ready-for-agent |
| 15 | Konfigurera LANTMATERIET_KEY i .env | ready-for-human |
| 16 | Ladda upp satellit­bild till Supabase Storage per lead | ready-for-agent |
