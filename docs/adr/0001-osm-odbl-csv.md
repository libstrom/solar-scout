# ADR 0001 — OSM ODbL och CSV-export-tolkning

**Status:** Draft (väntar jurist-verifiering)
**Beslutsdatum:** 2026-05-17
**Beslutsfattare:** Linus Bergström

## Kontext

Solar-scout använder OpenStreetMap-data (via Overpass API) för att:
1. Identifiera byggnader inom ett geografiskt område (`_get_osm_buildings`)
2. Hämta solpanel-taggar (`generator:source=solar`, `roof:solar_panel=yes`) som direkt-fynd
3. Hämta adressuppgifter från `addr:*`-taggar för att tagga upp byggnaderna

OSM-data licensieras under Open Database License (ODbL) 1.0. Licensen kräver:
- **Attribution** (§4.3): Bekräfta OSM och dess bidragsgivare vid varje publicerad användning av deriverat dataset
- **Share-Alike** (§4.4): Om man distribuerar ett "Derived Database", måste det också licensieras under ODbL
- **Keep open** (§4.5): Inga tekniska åtgärder får hindra mottagare från att utöva sina rättigheter

Tvetydigheten: när vi exporterar en CSV med lead-information (adress + lat/lng + AI-detektion + några OSM-härledda fält som byggnads-area), räknas det som ett "Derived Database" som triggar Share-Alike?

## Beslutsalternativ övervägda

### A. Aggressiv tolkning — "CSV är inte Derived Database"

Argumentet: en CSV med fåtal rader är inte en "Database" i ODbL:s mening. Det är ett urval/extract, inte ett strukturerat dataset avsett för fortsatt frågning.

**För:** Maximalt kommersiellt utrymme — kund kan använda CSV utan att vara ODbL-bunden
**Mot:** ODbL:s definition av "Database" är vid: "a collection of independent works, data or other materials arranged in a systematic or methodical way". En tabell med koord+adress + filtrerat ut ur OSM uppfyller detta.

### B. Konservativ tolkning — "CSV ÄR Derived Database, applicera Share-Alike"

Argumentet: Vi extraherar och kombinerar OSM-data (adress + koordinater + byggnadstyp + area) till ett systematiskt utvinningstillfälle. Det är en "Derived Database".

**För:** Tekniskt korrekt enligt ODbL § 4.4 (b)
**Mot:** Kund (batteri-säljbolag) får sin CSV under ODbL = de kan vidaredistribuera den fritt. Försvårar kommersialisering.

### C. Mellanväg — "Lägg på attribution men hävda 'Produced Work', inte 'Derived Database'"

ODbL skiljer mellan "Derived Database" (Share-Alike krävs) och "Produced Work" (bara attribution krävs). En "Produced Work" är ett resultat som skapats *från* databasen (t.ex. en rapport, en visualisering, en analys).

Argumentet: vår CSV är ett *resultat* av en analys (vi har lagt på AI-detektion + filtering), inte ett rent extract.

**För:** Realistisk balans — attribution är klart krav, Share-Alike kringgås
**Mot:** Gränsdragningen mellan Derived Database och Produced Work är jurist-grå-zon. Risk att jurist-tolkar att vår CSV är för-rådata.

## Beslut

**Vi väljer alternativ C (Produced Work) med tilläggsskydd:**

1. **Attribution synligt i CSV:** Varje CSV-export inleds med en kommentarrad (CSV-konvention: rad börjar med `#`):
   ```
   # Innehåller OSM-data © OpenStreetMap-bidragsgivare, ODbL 1.0
   # https://www.openstreetmap.org/copyright
   ```
   Plus en attribution-fotnot i appens UI under tabellvisning.

2. **CSV-innehåll positioneras som Produced Work:** vi inkluderar AI-konfidensvärden, samtomt-flaggor, och våra egna distill-fält bredvid OSM-deriverad rådata. Det blir ett analys-resultat, inte ett rent OSM-uttag.

3. **Privacy policy uppdaterad** med tydlig OSM-attribution i sektionen om datakällor.

4. **Tjänstevillkor för externa kunder** ska innehålla en klausul att kunden får använda CSV-data internt men inte återpublicera den som ett "öppet" dataset (vilket skulle trigga Share-Alike-frågan på deras sida).

5. **Jurist-granskning krävs** innan första externa kund signas. Denna ADR är ett utkast som ska valideras.

## Konsekvenser

- Vi måste alltid inkludera OSM-attribution i CSV-output (kodkrav)
- Vi måste alltid inkludera OSM-attribution synligt i UI:t (klart i v1 footer)
- Vi måste konsultera jurist om tolkningen håller — om jurist säger "nej, det är Derived Database" → vi kanske måste:
  - Antingen acceptera Share-Alike och anpassa affärsmodellen
  - Eller distansera oss MER från råextrakt (mer transformation av data innan leverans)
- Sub-processor-information måste inkludera OpenStreetMap Foundation som data-källa (för DPA Bilaga B)

## Källor

- [OpenStreetMap ODbL 1.0 — official text](https://opendatacommons.org/licenses/odbl/1-0/)
- [OSM Wiki — Produced Work–Derivative Database](https://wiki.openstreetmap.org/wiki/Open_Database_License/Substantial_-_Guideline)
- [OSM Foundation Licence & Legal FAQ](https://osmfoundation.org/wiki/Licence/Community_Guidelines)

## Status och nästa steg

- [ ] Skicka ADR till jurist tillsammans med DPA-mallen för kombinerad granskning
- [ ] Implementera CSV-attribution-rad i `app.py` (Slice 8)
- [ ] Verifiera att existerande footer-attribution är tillräcklig synlighet
- [ ] Inkludera OSM Foundation i DPA Bilaga B sub-processor-lista

---

*Denna ADR är en arbetsversion. Tolkningen i alternativ C kan visa sig fel efter jurist-granskning — i så fall uppdatera till alternativ B och anpassa affärsmodell.*
