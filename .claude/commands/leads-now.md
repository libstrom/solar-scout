---
name: leads-now
description: Get David actual solar leads as fast as possible. Decision tree for which city to scan, what to check if 0 leads, and how to export. Use when the team needs leads urgently or asks where to start scanning.
---

# leads-now — Snabbaste vägen till solcellsleads

## Steg 1 — Är appen uppe?

- Kontrollera Railway-appen på https://railway.app — ska vara grön/aktiv
- Om nere: kolla Railway-loggarna för felmeddelanden
- Alternativ lokal start: `streamlit run app.py` i solar-scout-mappen

## Steg 2 — Välj stad

Bästa städer just nu (hög villa-täthet, bra OSM-täckning):

| Stad | Varför bra | Bbox att använda |
|------|-----------|-----------------|
| **Nässjö** | Tät villabebyggelse SE3-zonen | 57.60, 16.18 → 57.65, 16.25 |
| **Huskvarna** | Blandad bebyggelse, många villor | 57.77, 14.26 → 57.82, 14.33 |
| **Vetlanda** | Glesbygd, bra för fallback-scan | 57.40, 15.04 → 57.45, 15.11 |

**Rekommendation:** Börja med Nässjö — ger konsekvent 15–40 leads per scan.

## Steg 3 — Kör scanning

1. Öppna appen → fliken **Scan**
2. Skriv in stadens namn eller rita bbox på kartan
3. Klicka **Starta scanning**
4. Vänta 2–5 min (statusbar visar framsteg)

## Steg 4 — Om 0 leads

Kör `/scan-debug` direkt. Vanliga orsaker:
- Overpass är tillfälligt nere → vänta 2 min + försök igen
- Fel stad angiven → prova Nässjö med explicit bbox
- API-nyckel slut → du/Linus får mail automatiskt

Prova alternativ stad: om Nässjö ger 0, testa Huskvarna eller Vetlanda.

## Steg 5 — Exportera leads

1. Gå till fliken **Leads**
2. Klicka **Exportera** (Excel/CSV-knapp högst upp)
3. Filen laddas ner med adress, koordinater, confidence och AI-resonemang

## Snabb-referens

- **0 leads:** `/scan-debug` → isolera vilket steg som fallerar
- **Vill ha fler leads:** Utöka bbox eller scanna grannstäder
- **Leads behöver granskas:** Leads med `needs_review=True` hamnar i granskningskön
- **Exportformat:** Excel (.xlsx) med alla fält inklusive Google Maps-länk
