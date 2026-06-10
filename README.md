# Solar Scout

End-to-end leadmaskin för solcellsförsäljning:

1. **`harvester.py`** hittar byggnader via Google Solar API och laddar ner
   satellitbilder — med live progress bar, ETA och kostnadsmätare.
2. **`prescreen.py`** AI-graderar varje tak (0–100) med en vision-modell och
   flaggar tak som redan har solceller.
3. **`app.py`** (Streamlit) — verifiera taken (bästa AI-score först), bygg
   ringlista med telefon/ägare/status, exportera till Excel.

```
harvester.py ──▶ leads.db ──▶ prescreen.py ──▶ Verification Lab ──▶ Ringlista ──▶ Excel ──▶ bokade möten
   (scan + ETA)                  (AI-score)        (människa)        (telefon)
```

## Kör

```powershell
# 1. Installera beroenden (en gång).
pip install -r requirements.txt

# 2. Lägg in din Google Maps API-nyckel (Solar + Static Maps + Geocoding aktiverade).
copy .env.example .env
# redigera .env och klistra in GOOGLE_MAPS_API_KEY

# 3. Nattskörd — upp till 200 byggnader i Kågeröd som default.
#    Live progress bar med ETA + kostnad i terminalen; samma siffror
#    syns i appens Dashboard medan skörden pågår.
python harvester.py

# 4. AI-gradera taken. Backend väljs automatiskt: PIONEER_API_KEY i .env
#    -> Pioneer AI (~6 s/tak), annars ANTHROPIC_API_KEY -> Anthropic API,
#    annars Claude Code CLI (din prenumeration, ~20 s/tak).
python prescreen.py

# 5. Verifiera, ring och exportera i webbläsaren.
streamlit run app.py
```

`harvester.py`-flaggor:
- `--town Kågeröd` (default) eller `--town Svalöv`
- `--max-buildings 200` (default; höj för längre körning)

`prescreen.py`-flaggor:
- `--limit N` — gradera bara N tak
- `--backend pioneer|api|cli` — tvinga backend (default: pioneer > api > cli efter vilka nycklar som finns)
- `--redo` — gradera om redan bedömda

Båda är resume-säkra: redan skördade/graderade rader hoppas över vid omkörning.
Ctrl+C mitt i en skörd är ofarligt — läget sparas och nästa körning fortsätter.

## Vad som lagras

`data/leads.db` (SQLite) — tabellen `leads`:

| Kolumn | Anteckning |
|---|---|
| `place_id` | Solar API-byggnads-id (t.ex. `buildings/ChIJ...`). |
| `address` | Omvänt geokodad gatuadress. |
| `lat`, `lng`, `coordinates` | Byggnadens centrum. |
| `solar_confidence` | Solar API `imageryQuality`: `HIGH` / `MEDIUM` / `LOW`. |
| `roof_area_m2` | Från `solarPotential.wholeRoofStats.areaMeters2`. |
| `image_path` | Lokal 600×600 PNG-satellitbild. |
| `status` | `pending` / `confirmed` / `rejected`. |
| `ai_score` | AI-bedömning 0–100 (0 = paneler finns redan). |
| `ai_has_panels` | 1 om AI:n ser befintliga solpaneler. |
| `ai_reason` | AI:ns korta motivering på svenska. |
| `phone`, `owner_name` | Ringlista-fält (fylls i appen, slå upp via MrKoll). |
| `call_status` | Att ringa / Uppringd / Bokad / Nej tack. |
| `call_notes` | Fritext från ringlistan. |
| `raw_solar_data` | Hela JSON-svaret från Solar API. |

Tabellen `scan_runs` håller live-progress för pågående skörd (grid-punkter,
nya leads, ETA, kostnad) — det är den appens Dashboard-panel läser.

`data/images/` innehåller en PNG per skördad byggnad.

## Excel-exportens kolumner

Endast bekräftade leads: **Address**, **Plus Code** (beräknas lokalt),
**Estimated Fuse (A)** (heuristik från takyta: `<100 m² → 16 A`,
`100–150 → 20 A`, `150–200 → 25 A`, `≥200 → 35 A` — grov proxy, ej
auktoritativ), **Telefon**, **Ägare**, **Ringstatus**, **AI Score**,
**MrKoll Link**, **Maps Link**.

## Tester

```powershell
python -m pytest tests/ -q
```

86 tester: grid-matematik, DB-lager, migrationer, scan_runs-flödet,
ETA-beräkning, AI-svarstolkning, Excel-byggaren.

## Kostnadsvakt

Ett 20m-grid över default-Kågeröd-bboxen är ~14 000 grid-punkter. Harvestern
skriver ut värsta-fallet vid start och visar löpande kostnad i progress-baren.
Med `--max-buildings 200` (default) stannar verklig kostnad på några USD.

## Säkerhetsnot

Rotera Google Maps-nyckeln i Cloud Console efter utveckling — om den någonsin
klistrats in i chatt eller screenshot, betrakta den som exponerad. `.env`
ligger i `.gitignore`.
