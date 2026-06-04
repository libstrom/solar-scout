# migrate-to-edek.ps1
# Kopierar energideklarations-pipelinen fran solar-scout till edek-parser.
# Kör en gång lokalt, sedan startar du en ny Claude Code-session i edek-parser-mappen.
#
# Krav: git installerat, inloggad på GitHub (git push funkar)
#
# Körs från solar-scout-mappen:
#   cd C:\path\to\solar-scout
#   .\migrate-to-edek.ps1

param(
    [string]$EdekRepo   = "https://github.com/libstrom/edek-parser.git",
    [string]$EdekDir    = "..\edek-parser",
    [string]$SolarDir   = $PSScriptRoot
)

$ErrorActionPreference = "Stop"

function OK($m)  { Write-Host "OK  $m" -ForegroundColor Green }
function INF($m) { Write-Host "    $m" -ForegroundColor DarkGray }
function HDR($m) { Write-Host "`n--- $m ---" -ForegroundColor Cyan }

HDR "Steg 1: Klona edek-parser"
if (Test-Path $EdekDir) {
    INF "Mappen finns redan — kör git pull istallet"
    Set-Location $EdekDir
    git pull origin main
    Set-Location $SolarDir
} else {
    git clone $EdekRepo $EdekDir
}
OK "edek-parser klonad till $EdekDir"

HDR "Steg 2: Kopiera pipeline-filer"
$files = @(
    "batchXlsm.mjs",
    "xlsm.mjs",
    "inspectXlsm.mjs",
    "enrichLeads.mjs",
    "peekTab.mjs",
    "tabToCSV.mjs",
    "unlockXlsm.py",
    "extractEnergyPdf.py",
    "mergeEnergy.py",
    "makeLeads.py",
    "enrichContacts.py",
    "exportEnergyList.py",
    "debugMatch.py",
    "generateOneDriveLinks.ps1",
    "kor.ps1",
    "setup.ps1",
    "HANDOFF-edek-parser.md"
)

foreach ($f in $files) {
    $src = Join-Path $SolarDir $f
    # HANDOFF-edek-parser.md byter namn till HANDOFF.md i maldrepot
    $dstName = if ($f -eq "HANDOFF-edek-parser.md") { "HANDOFF.md" } else { $f }
    $dst = Join-Path $EdekDir $dstName
    if (Test-Path $src) {
        Copy-Item $src $dst -Force
        INF "Kopierade $f -> $dstName"
    } else {
        Write-Host "VARNING: $f hittades inte i solar-scout" -ForegroundColor Yellow
    }
}
OK "Pipeline-filer kopierade"

HDR "Steg 3: Skapa requirements.txt for edek-parser"
@"
openpyxl>=3.1.5
pypdf>=4.0.0
playwright>=1.44.0
requests>=2.32.0
beautifulsoup4>=4.12.0
lxml>=5.0.0
msoffcrypto-tool>=3.0.0
"@ | Set-Content (Join-Path $EdekDir "requirements.txt") -Encoding UTF8
OK "requirements.txt skapad"

HDR "Steg 4: Skapa .gitignore"
@"
*.json
!package.json
!skills-lock.json
node_modules/
__pycache__/
*.pyc
.env
.venv/
*.xlsx
*.tab
*.csv
"@ | Set-Content (Join-Path $EdekDir ".gitignore") -Encoding UTF8
OK ".gitignore skapad"

HDR "Steg 5: Skapa CLAUDE.md"
@"
# edek-parser

## Syfte

Utvinner, forädlar och exporterar data ur Energivision energideklarationer
(XLSM + PDF) fran en OneDrive-mapp till poängsatt ringlista (leads.xlsx)
och FileMaker-export (energy-list.xlsx).

## Pipeline

```
OneDrive/
  Energideklarationer/*.xlsm  -->  node batchXlsm.mjs       -->  xlsm.json
  PDF Energidek/*.pdf         -->  python extractEnergyPdf.py --> pdf.json
                                   python mergeEnergy.py      --> energy-data.json
                                   python enrichContacts.py   --> (berikad med Hitta.se)
                                   python exportEnergyList.py --> energy-list.xlsx
                                   python makeLeads.py        --> leads.xlsx
```

Kör allt med: .\kor.ps1
Forsta gangen: .\setup.ps1

## Pipeline-status

- [ ] extractEnergyPdf.py  --> pdf.json
- [ ] batchXlsm.mjs        --> xlsm.json
- [ ] mergeEnergy.py        --> energy-data.json
- [ ] enrichContacts.py     --> kontaktuppgifter fran Hitta.se
- [ ] exportEnergyList.py   --> energy-list.xlsx (FileMaker)
- [ ] makeLeads.py          --> leads.xlsx (ringlista)

## Regler

- Committa och pusha varje forandring direkt.
- Kör alltid parallella agenter for oberoende deluppgifter.
- Parser-exportnamn: extractXlsmFields (inte extractXlsm).
- Levererara kodfiler med Write-verktyget, aldrig base64 i chatten.

## Nyckeldata

enspecta.tab: 47 249 rader, 30 kolumner. Ligger lokalt pa Windows, ej i repot.
energy-data.json: genereras lokalt, ej i repot (.gitignore).
leads.xlsx / energy-list.xlsx: genereras lokalt, ej i repot.

## Kontaktprioritet (enspecta.tab)

1. Köpare (nuvarande agare, bast)
2. Intressent fran Saljarbesiktning (trolig kopare, bra)
3. Säljare (har flyttat, samst)
"@ | Set-Content (Join-Path $EdekDir "CLAUDE.md") -Encoding UTF8
OK "CLAUDE.md skapad"

HDR "Steg 6: Skapa CONTEXT.md"
@"
# edek-parser -- domaänkontext

## Produkt i ett mening

Pipeline som lasear Energivision energideklarationer (XLSM + PDF),
extraherar energidata och kontaktuppgifter, och genererar en poangsatt
ringlista (leads.xlsx) for forsaljning av solceller och batterilager.

## Aktorer

| Roll | Namn | Ansvar |
|------|------|--------|
| Fältsäljare | David | Ringer leads, fyller i status i leads.xlsx |
| Produktagare | Linus Bergstrom | Kor pipeline, granskar output |
| Datakalla | Afif | Levererar hårddisk/OneDrive med energideklarationer |

## Domäntermer

| Term | Betydelse |
|------|-----------|
| energideklaration | Officiellt dokument (Energivision XLSM eller PDF) per fastighet |
| fastighetsbeteckning | Unik nyckel per fastighet, t.ex. "lund husie 12:34" |
| energiklass | A-G fran energideklarationen |
| score | Beraknat poangvarde 0-100+ per lead baserat pa energiklass, elforbrukning, byggår |
| bucket | Kategori: SOL, BATTERI, SOL+BATTERI |
| har_solceller | Bool fran energideklarationen -- styr bucket-tilldelning |
| enspecta.tab | Register fran FileMaker, 47 249 rader, ger kontaktuppgifter per fastighetsbeteckning |
| energy-data.json | Merged JSON fran XLSM + PDF, 1 282 unika fastigheter |
| Dödsbo | Dödsbo som agare -- pitch-text anpassas, flaggas i Anteckningar |

## Kanda begransningar

- 158 av 1 282 energideklarationer matchar enspecta.tab (12%)
- Resterande 1 124 saknar kontaktuppgifter -- bestriks med enrichContacts.py (Hitta.se)
- PDF-poster saknar ofta adress -- endast fastighetsbeteckning finns saakert

## Datakallor

| Kalla | Format | Innehall |
|-------|--------|----------|
| OneDrive XLSM | Energivision .xlsm | Rik energidata + adress + atgardsforslag |
| OneDrive PDF | Energivision .pdf | Energiklass + kWh/m2, saknar ofta adress |
| enspecta.tab | Tab-separerad, 47 249 rader | Kontaktuppgifter per fastighetsbeteckning |
| Hitta.se | Webb (Playwright) | Kompletterande telefonnummer |
"@ | Set-Content (Join-Path $EdekDir "CONTEXT.md") -Encoding UTF8
OK "CONTEXT.md skapad"

HDR "Steg 7: Commit och push till edek-parser"
Set-Location $EdekDir
git add .
git commit -m "feat: importera energideklarations-pipeline fran solar-scout

Innehaller:
- XLSM-parser (batchXlsm.mjs, xlsm.mjs, inspectXlsm.mjs, unlockXlsm.py)
- PDF-extraktor (extractEnergyPdf.py)
- Merge-skript (mergeEnergy.py)
- Lead-generator (makeLeads.py)
- Kontaktberikning via Hitta.se/Playwright (enrichContacts.py)
- FileMaker-export (exportEnergyList.py)
- Windows-pipeline (kor.ps1, setup.ps1)
- Diagnostik (debugMatch.py, peekTab.mjs, tabToCSV.mjs)"
git push origin main
Set-Location $SolarDir
OK "edek-parser uppdaterad pa GitHub"

Write-Host ""
Write-Host "=== Migration klar! ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Naasta steg:" -ForegroundColor Yellow
Write-Host "  1. cd $EdekDir"
Write-Host "  2. Starta en ny Claude Code-session:"
Write-Host "     claude  (i edek-parser-mappen)"
Write-Host "  3. Lagg till enspecta.tab i edek-parser-mappen (kopieras, ligger kvar i solar-scout)"
Write-Host ""
Write-Host "Solar-scout-repot aer oroart -- inga filer raderades darifraan." -ForegroundColor DarkGray
