# Handoff — 2026-05-17 grilling + V1.5 PRD-session

**From:** Claude Opus 4.7 (1M context)
**To:** Next agent or fresh session
**Conversation arc:** ~6 hours, started with repo-rename request, ended with PRD v1.5 + 6 commits pushed
**State at handoff:** PR #2 has 6 commits, all v1-ready. PRD v1.5 published as `docs/prd/v1.5-compliance-first.md`. Pilot starts Monday 2026-05-18.

## TL;DR for the next agent

User is **Linus Bergström** — marknadsföringsansvarig/affärsutvecklare at a Swedish battery-installer company. He built solar-scout privately and is now licensing it to the company. Currently running internal pilot to validate before extern SaaS expansion.

V1 is **shipped**. V1.5 PRD is **written and pushed** but blocked on user-side HITL config (Lantmäteriet token, Supabase bucket, Swedma Robinsonlistan-konto). Don't start coding V1.5 yet — wait for pilot results first (decision point: ~tisdag 2026-05-19 after first scan-results visible).

## Stakeholder map

| Person | Roll | Beslut/aktion |
|---|---|---|
| **Ibrahim** | Chef / beslutsfattare | "Kör så länge det ger leads. Vi tittar på betald AI om 1 dag" (sagt torsdag 2026-05-14) |
| **Mehrdad** | Säljcoach | Kontroll över Davids tid. Behöver buy-in för pilot. |
| **David** | Mötesbokare | End-user. Ringer 50 leads, bokar möten. Får CSV varje måndag. |
| **Linus Bergström** | Marknadsförings-/affärsutveckling | User (ägare av solar-scout-IP) |

## Product premise (känd nu, var fel-antagen i början av sessionen)

V1 är **batteri-uppsälj till villaägare med befintliga solpaneler**. NOT "find houses without solar to sell new solar to" (det var mitt fel-antagande tidigt — koden visar tydligt "Hittade X tak med solceller" + `has_solar="Ja"` + `air_to_air/air_to_water`-fält).

V2 (framtid): scanna hus **utan** solpaneler för **solinstallatörer** som kund-segment. Separat produkt, egen PRD.

## V1 status

- ✅ Renamed edek-parser → solar-scout
- ✅ Läckt deploy-token borttagen från `.claude/settings.json` (men är kvar i git-historik — **användaren måste rotera** den manuellt)
- ✅ Scanner tightened: building deny-list, area-filter 40–600 m², addr-snap inom 25m, OSM `out geom`-fix
- ✅ AI-prompt: 2-stegs HOUSE→SOLAR med "describe then verdict"-mönster, max_tokens=180, F1=100% på 6 baseline-fall
- ✅ Crop default 50m → 18m (panel-grid syns nu)
- ✅ ANTHROPIC_API_KEY → `SOLAR_SCOUT_ANTHROPIC_KEY` med fallback (skyddar mot Claude Code billing-swap)
- ✅ MrKoll-länkar → Google + Hitta-länkar i CSV
- ✅ Attribution + GDPR-footer-länk
- ✅ 3 pitchar + licens-avtal-skiss skrivna (`docs/pitches/`)

## V1.5 status — NOT YET STARTED

PRD är i `docs/prd/v1.5-compliance-first.md`. Åtta vertical slices, compliance-first.

HITL-blockerare som måste lösas av användaren INNAN slice-arbete:

1. **Lantmäteriet:** Registrera konto på geotorget.lantmateriet.se → beställa "Ortofoto öppna data Visning" → få `LANTMATERIET_KEY`. ~30 min user time.
2. **Supabase Storage-bucket:** Skapa bucket "lead-images" med RLS per user_id. ~30 sek user time.
3. **Swedma Robinsonlistan-konto:** Avtal för API-tillgång. ~1 dag inkl. kontraktssignering.
4. **Jurist-granskning** av DPA-mall + ODbL CSV-tolkning. ~1 vecka extern.
5. **Stripe-priser** för credit-pack (om credit-modell aktiveras — defer V1.5).

## V1.7 3D-flygvy locked

Specat i PRD:
- Embedded Cesium-viewer i Streamlit-component
- Google Photorealistic 3D Tiles som datakälla
- Modal med ESC + click-outside-close
- Hard-coded city-lista för täckning (Stockholm, Göteborg, Malmö, ...)
- 2-3 dagars utveckling
- Knappikon "3D-flygvy" 🌐 bredvid LM-thumbnail per lead

NOT v1.5 — v1.7 efter compliance + kvalitet.

## Critical design decisions (gjorda 2026-05-17)

### Pris-modell

- **Marknadspris kalla pre-filtered leads:** 25–75 kr/lead (validerat mot Bisnode 5–20 kr/lead, Leadagenten 200–800 kr/lead för varma)
- **Vår positionering:** 75 kr/lead Premium-tier (med bild-URL + Google/Hitta-länk)
- **TG på vår sida:** ~96% (marginalkostnad 3 kr/lead, primärt Anthropic AI)
- **Internt licens-pris:** 10k kr/mån månadslicens till företaget, eller 150–400k kr engångsköp av IP (Linus väljer i förhandling med Ibrahim)

### Lead-distribution-strategi

Långsiktig: layered pricing per Apollo/LendingTree-modell:
- Layer 1: Exclusive (1 köpare) — bas × 3-4
- Layer 2: Semi-exclusive (2-3 köpare per region) — bas × 1.5-2
- Layer 3: Shared (open) — bas × 0.5-1
- Plus tidsbaserad åldring: fresh (≤24h) 100%, recent 70%, aged 40%, old 20%

V1.5: bara exclusive (1 intern kund). Layering aktiveras vid kund #2.

### Marknadssatuering-skydd (V2/V3)

Sverige har ~140 000 villor med sol utan batteri. Med 50+ kunder × 200 leads/mån = marknaden bränd på ~1 år. Måste byggas in från start för hållbarhet:

- Globalt lead-dedup (kund A's lead säljs inte till kund B)
- 12-månaders kontakt-cooldown per fastighet
- Customer-count cap per land (max ~10-20 kunder)
- Geografisk exklusivitet per kommun

V1.5: ej implementerat (irrelevant med 1 kund). V2 kritiskt.

## Open questions for user (efter pilot)

1. **Vykortsbild med snedvy** — om V1.7 3D bygges, ska QR-vykortet (V2 warm-up) också ha 3D-bild på framsidan? (UX-fråga)
2. **Lantbruk-segmentet** — fortsatt confirmed som V2 egen produkt. När? (timing-fråga, beror på V1.5-validerings-resultat)
3. **Bisnode/UC/Ratsit person-data API** — för Pro-tier (varma leads) behöver vi avtal. Vilken vendor? (procurement-fråga)

## Files modified this session

```
.claude/settings.json                          # token removed, path generalized
.gitignore                                     # .claude/settings.local.json + .env.local
CLAUDE.md                                      # edek-parser → solar-scout
docs/agents/issue-tracker.md                   # rename
app.py                                         # MrKoll→Google+Hitta, footer, urllib import
                                               # ANTHROPIC_API_KEY → SOLAR_SCOUT_ANTHROPIC_KEY
scanner.py                                     # deny-list, area-filter, addr-snap, out geom,
                                               # describe-then-verdict prompt, crop 50→18m,
                                               # max_tokens 80→180, User-Agent header
test_scanner.py                                # _analyze_building integration, F1-suite,
                                               # 6 test cases (Nässjö + Lund + Växjö mix)
.env.example                                   # SOLAR_SCOUT_ANTHROPIC_KEY documented
docs/pitches/pitch_mehrdad.md                  # ny
docs/pitches/pitch_ibrahim.md                  # ny
docs/pitches/licens_avtal_skiss.md             # ny
docs/prd/v1.5-compliance-first.md              # ny — kanonisk PRD
docs/handoff/2026-05-17-grilling-and-v1.5-prd.md  # detta dokument
```

## Commits on PR #2

```
ad3e... rename-to-solar-scout (squashed initial)
7a4f... scanner accuracy + filters
e892... prompt v2 grid+colour
63f94e... SOLAR_SCOUT_ANTHROPIC_KEY rename
a8eb07... zoom + describe-verdict + max_tokens (F1=100%)
2be3b1... TEST_CASES Skåne+Småland mix
a7f732... MrKoll→Google+Hitta + attribution + 3 pitches
f830bf... PRD v1.5 compliance-first
```

## Test data (för F1-regression)

```
TEST_CASES i test_scanner.py:
- Rågången 81, Nässjö (TP)
- Körsbärsstigen 1, Nässjö (TP)
- Hultgatan 1, Nässjö (TP)
- Plommonvägen 1, Lund (TP, user-verified)
- Östra Ringvägen 34, Växjö (TN, user-verified)
- Vilhelmsrogatan 3, Nässjö (TN, user-verified)

(Plommonvägen 3 + Handskmakaregatan 1A Lund pending labels.)
(Skatgatan 5 Jönköping: panels on garage, parked behind comment as edge case.)
```

## Known issues / gotchas

- **Läckt deploy-token i git-historik:** Den läckta `64512e5e-dda5-4e45-82ef-e4ab6db8ced7` är borttagen ur filen men kvar i git-log. Token är nu obsolet (tjänsten borttagen) men bör ändå betraktas som komprometterad.
- **ANTHROPIC_API_KEY i chatt-logg:** `sk-ant-api03-df7-ksLD2k...` användes för test_scanner-validering. Användaren ska revoke:a den efter session. Sa "senare" när jag påminde.
- **GitHub MCP token expired:** Issue #3 (gamla PRD-utkast med credit-modell) kunde inte uppdateras. PRD-filen är nu canonical.
- **Anthropic prompt caching:** Försökte men prompten är ~250 tokens, under 1024-tokenströskeln. Skippad.
- **Mapbox storage 24h limit:** HÅRD regel — image-pipeline får INTE lagra Mapbox-bilder, bara LM.

## What the next agent should do

**If pilot is positive (≥1% lead-to-sale konversion på 50 leads):**
1. Kolla med Linus om HITL-blockerare löst (Lantmäteriet-token, Supabase-bucket, Swedma-konto)
2. Börja på V1.5 Slice 1 (Lantmäteriet API-byte) — det är compliance-blocker, måste först
3. Sen Slice 2 (privacy page) — quick win, 1 h
4. Sen Slice 3 (Robinsonlistan) — blockerar extern lansering
5. Parallellt: Slice 4 (DPA-mall) skickas till jurist

**If pilot is negative (<1% konversion):**
1. Diagnos: var i funneln dog det? (samtal-svar-rate? bokningsgrad? sälj-konvertering?)
2. Om svar-rate låg → byt strategi från brev till annan kanal
3. Om bokningsgrad låg → undersök lead-kvalitet (är AI-träffarna verkligen villaägare med sol?)
4. Om sälj-konvertering låg → produkt-marknadsfit-problem, inte lead-problem
5. Pausa V1.5-utveckling, gör post-mortem med Linus + Mehrdad + David

**Don't:**
- Bygga V1.5 utan pilot-validering
- Implementera credit-modell utan första extern kund
- Lagra Mapbox/Google-bilder permanent
- Byta från `minkarta` till `api.lantmateriet.se` utan att verifiera token + F1-regression
- Skapa nya GitHub-issues utan att kolla att MCP-token är giltig

## Last user state

Användaren pushade tillbaka på flera av mina premature optimerings-förslag (credit-modell, warm-up, samtomt-check-as-exclusion). Detta är POSITIVT — användaren tänker som en bra PM. Honor it: don't add scope without explicit ask.

Avslutade med 3D-flygvy-decision (V1.7), markställning-på-villatomt-spec (V1.5 inkluderat), lantbruk-defer (V2 egen produkt), PRD-format (fil ej GitHub-issue).

Kompakt + branch-state: PR #2 är aktiv och cleant — kan merge:as till main efter pilot-validering. V1.5-arbete blir separat PR från main.

## Gap-analys 2026-05-17 (via /prototype-skill)

Throwaway-prototyp pushade en Lead genom 5-fas-lifecycle (scan → delivered → contacted → meeting → site_visited → sold/lost) inkl. hårda fall (dup, samtomt, NIX-klagomål, låg-konf-träff, no-answer-retry).

**Pilot-blockerare:** INGEN. David kompenserar manuellt för alla luckor. Linus mäter konvertering manuellt under pilot.

**V1.5-followup (post-pilot, ~1-2 dagars jobb):**
1. `do_not_contact`-flagga i `scout_leads` + UI-knapp i `page_leads` (~1h) — KRITISK för repeterade scans, annars ringer David samma personer igen
2. AI-konfidens-tröskel-filter (~30min) — låg-konf-leads (<0.7?) flaggas/exkluderas
3. Auto-dup-droppning vid scan (~1h) — kolla nya leads mot befintliga `scout_leads` med samma koord
4. Sales-pipeline-status-kolumn (~3h) — contacted/booked/sold/lost utöver befintlig `user_confirmed`
5. Konverterings-dashboard i `page_leads` (~2h) — automatisera 14-dagars-rapport

**V1.6 (efter pilot bevisat):**
6. Phone-enrichment via Bisnode/Hitta-API (kräver avtal)
7. Salessumma + TB-rapport per lead → ROI-dashboard för Ibrahim

Prototypen ligger i `/tmp/solar-scout-prototype/lead_lifecycle.py` (throwaway, NOT i repo). Kör med `python3 /tmp/solar-scout-prototype/lead_lifecycle.py` för att reproducera gap-analysen.
