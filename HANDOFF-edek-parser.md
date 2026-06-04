# edek-parser — Handoff (2026-06-04)

## Vad är det här repot?

edek-parser utvinner, förädlar och exporterar data ur Energivision energideklarationer
(XLSM + PDF) till en poängsatt ringlista för försäljning av solceller och batterilager.

Det är ett **fristående pipeline-repo** utan koppling till solar-scout-appen.
Solar-scout (visuell scanner + Streamlit) och edek-parser är två separata leadkällor
som båda matar till samma säljteam (David) men aldrig delar kod.

---

## Dataflöde — komplett bild

```
OneDrive (Windows)
  Enspecta Energideklarationer/
    *.xlsm  (1 692 filer)          -->  node batchXlsm.mjs <mapp>   -->  xlsm.json
    PDF Energidek/*.pdf (3 242 filer) --> python extractEnergyPdf.py  -->  pdf.json

xlsm.json + pdf.json               -->  python mergeEnergy.py        -->  energy-data.json
                                        (1 282 unika fastigheter)

energy-data.json                   -->  python enrichContacts.py     -->  (berikad in-place)
                                        (Playwright + Hitta.se)

energy-data.json                   -->  python exportEnergyList.py   -->  energy-list.xlsx
                                        (alla 1 282 rader, för FileMaker)

energy-data.json + enspecta.tab    -->  python makeLeads.py          -->  leads.xlsx
                                        (poängsorterad ringlista)
```

Kör hela kedjan med ett kommando: `.\kor.ps1`

---

## Primära datakällor

### 1. Energivision XLSM-filer (rik data)
Excelfiler genererade av Energivision-programvaran. Innehåller:
- Fastighetsbeteckning, adress, postnr, ort, kommun
- Energiklass (A-G direkt ur cell)
- Energiprestanda kWh/m², el per källa (direkt, vattenburen)
- Uppvärmningssystem, åtgärdsförslag
- har_solceller, har_solvarme (bool)
- Deklarationsdatum, nybyggnadsår, Atemp m²

Parser: `xlsm.mjs` (in-memory ZIP, inga tempdirs).
Export: `extractXlsmFields` (viktigt — inte `extractXlsm`).
Sheet-prioritet: `['inmatning', 'certifikat', 'indata', 'rapport', 'deklaration', 'data']`
Inmatning måste vara först — annars vinner indata över inmatning vid merge.

### 2. Energivision PDF-filer (enklare data)
PDF-versioner av samma deklarationer. Innehåller:
- Fastighetsbeteckning, energiklass, kWh/m²
- Byggår, Atemp, har_solceller, har_solvarme
- Saknar ofta adress, uppvärmningssystem, åtgärdsförslag

Parser: `extractEnergyPdf.py` (pypdf).

### 3. enspecta.tab (kontaktuppgifter)
Tab-separerad export från FileMaker, 47 249 rader, 30 kolumner.
Innehåller besiktningsposter med ägarens namn, telefon, email.
Ligger lokalt på Windows, checkas INTE in i repot.

Struktur:
- Kolumner 0-23: Huvudpost (CaseID, Status, datum, adress, fastighetsbeteckning, kontakt)
- Kolumner 24-28: Intressent (förnamn, efternamn, email, telefon1, telefon2)
- Tomma col0-rader: Intressenter kopplade till föregående CaseID via radposition

Kontaktprioritet:
1. Köpare (nuvarande ägare — bäst)
2. Intressent från Säljare-besiktning (trolig köpare — bra)
3. Säljare (har flyttat — sämst)

### 4. Hitta.se (kompletterande kontakt)
Används av `enrichContacts.py` för fastigheter som saknas i enspecta.tab.
Playwright kör riktig Chromium-webbläsare för att undvika bot-blockering.

---

## Matchningsläge och nuvarande täckning

| Källa | Antal |
|-------|-------|
| XLSM-filer på disk | 1 692 |
| PDF-filer på disk | 3 242 |
| Unika fastigheter efter merge | 1 282 |
| Matchade mot enspecta.tab | 158 (12%) |
| Aktiva leads i leads.xlsx | 139 |
| Saknar kontaktuppgifter | 1 124 |

Låg matchningsprocent (12%) beror på att energideklarationerna täcker ett
bredare fastighetsurval än vad Enspecta gjort besiktningar på.
De 1 124 utan kontakt bestrids med enrichContacts.py (Hitta.se) eller
manuell sökning i FileMaker via energy-list.xlsx.

---

## Normalisering av fastighetsbeteckning (kritisk detalj)

Nyckeln som kopplar ihop alla datakällor är fastighetsbeteckningen.
Den skrivs olika i olika källor och måste normaliseras identiskt överallt:

```python
def norm(s):
    s = str(s).lower().replace('_', ' ').replace(':', ' ')
    return re.sub(r'\s+', ' ', s).strip(' _-.')
```

Vanliga problem:
- XLSM: "öremölla_13_86_" vs enspecta.tab: "örremölla 13 86"
- Understreck och kolon måste bli mellanslag
- Avslutande skräptecken måste trimmas

Ogiltiga nycklar filtreras av `is_valid_fastig()`:
- Rena siffror ("123456")
- Etikett-text ("fastighetsbeteckning", "faktura...")
- För korta strängar (under 3 tecken)

Token-baserad fuzzy-matching som fallback:
`_fastig_tokens()` delar upp nyckeln i (namnord, frozenset-av-siffror)
för att matcha "lund husie 12 34" mot "lund husie 12:34".

---

## Scoring-modell (makeLeads.py)

Varje lead får ett poängvärde baserat på:

| Faktor | Poäng |
|--------|-------|
| Energiklass F | +25 |
| Energiklass G | +30 |
| Energiklass E | +15 |
| El-direktvärme > 10 000 kWh | +20 |
| El-direktvärme > 5 000 kWh | +12 |
| Byggår 1960-1979 | +15 |
| Byggår 1980-1995 | +10 |
| Köpare/Intressent | +30 |
| Besiktning senaste 2 år | +20 |
| Har telefon | +8 |
| Har e-post | +4 |

Bucket-tilldelning:
- har_solceller = True  --> "BATTERI (har sol)"
- score >= 65           --> "SOL + BATTERI"
- score >= 45           --> "SOL"
- övrigt               --> "BATTERI / VP"

---

## Dödsbo-hantering

När namn-fältet börjar med "Dödsbo":
- `_is_dodsbo()` detekterar det
- `pitch_text()` använder neutral hälsning ("Hej! Jag söker den som förvaltar...")
- `_build_row()` fyller Anteckningar med varning: "Dödsbo — fråga vem som förvaltar fastigheten"

---

## Output-filer

### leads.xlsx (ringlista för David)
6 flikar: Dashboard, TOP 50, Intressenter, Köpare, Säljare, Scoring.
26 kolumner inkl. Score, Bucket, Pitch-text, Energiklass, Besiktningsdatum,
länk till energideklarationsfilen (kolumn 25: Energideklaration).

### energy-list.xlsx (för FileMaker-sökning)
En rad per fastighet ur energy-data.json.
Sorterad energiklass A-G med färgkodning.
Innehåller kontaktkolumner om enrichContacts.py körts.
CSV-format tillgängligt via `python exportEnergyList.py energy-data.json output.csv`

---

## Filer i repot

| Fil | Vad |
|-----|-----|
| `batchXlsm.mjs` | Rekursivt batch-parsear XLSM-mapp |
| `xlsm.mjs` | XLSM-parser, in-memory ZIP, returnerar extractXlsmFields |
| `inspectXlsm.mjs` | Debug enskild XLSM-fil |
| `unlockXlsm.py` | Låser upp lösenordsskyddade XLSM med msoffcrypto-tool |
| `extractEnergyPdf.py` | Extraherar energidata ur PDF-deklarationer |
| `mergeEnergy.py` | Fältvis merge: XLSM vinner, PDF fyller luckor |
| `makeLeads.py` | Genererar leads.xlsx med scoring + pitch-text |
| `enrichContacts.py` | Berikad kontaktdata via Playwright + Hitta.se |
| `exportEnergyList.py` | Exporterar energy-data.json till Excel/CSV |
| `debugMatch.py` | Diagnostik för matchning mot enspecta.tab |
| `peekTab.mjs` | Visar kolumner i enspecta.tab med exempelvärden |
| `tabToCSV.mjs` | Konverterar enspecta.tab till CSV |
| `enrichLeads.mjs` | Berikar leads.json med kontaktdata från enspecta.tab |
| `generateOneDriveLinks.ps1` | Skapar file://-länkar till OneDrive-filer |
| `kor.ps1` | Kör hela pipelinen steg 1-6 med flaggor |
| `setup.ps1` | Engångsinställning: pip, playwright, node |

---

## Öppna frågor

1. **Afifs äldre system**: Finns det en Energivision-databas eller Access/Excel-register
   från innan FileMaker som har ägarkontakter till de 1 124 saknade fastigheterna?
   Fråga Afif specifikt om detta.

2. **enspecta.tab utan --energy-only**: makeLeads.py kan köras utan `--energy-only`
   och då används hela enspecta.tab (~10 000-16 000 leads) med energidata som bonus
   för de 158 matchade. Outforskat — kan ge mycket fler leads.

3. **Hitta.se täckning**: enrichContacts.py är otestad i produktion.
   Kör `--limit 20 --headed` första gången för att verifiera träffkvalitet.

4. **Pipeline i edek-parser vs solar-scout**: Ska energy-leads på sikt in i Supabase
   och solar-scout-appen så David ser allt i ett ställe? Öppen fråga.

---

## Nästa steg (rekommenderat)

1. Kör `.\setup.ps1` (engång)
2. Kör `.\kor.ps1` för att generera leads.xlsx och energy-list.xlsx
3. Testa `enrichContacts.py --limit 20 --headed` för Hitta.se-kvalitet
4. Kontakta Afif om äldre databas med kontaktuppgifter
5. Utvärdera `makeLeads.py` utan `--energy-only` för fler leads ur enspecta.tab
