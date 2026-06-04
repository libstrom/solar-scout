# kor.ps1 — Kör hela solar-scout pipeline
#
# Steg:
#   1. batchXlsm.mjs      → xlsm.json
#   2. extractEnergyPdf.py → pdf.json
#   3. mergeEnergy.py      → energy-data.json
#   4. enrichContacts.py   → lägger till telefon/namn från Hitta.se
#   5. exportEnergyList.py → energy-list.xlsx  (för FileMaker-sökning)
#   6. makeLeads.py        → leads.xlsx         (ringlista)
#
# Kör: .\kor.ps1
# Hoppa steg:  .\kor.ps1 -StartFran 4

param(
    [int]$StartFran = 1,
    [int]$HittaLimit = 0,        # 0 = alla, annars max X poster mot Hitta.se
    [switch]$HittaHeaded,        # Visa webbläsarfönstret vid Hitta-sökning
    [switch]$IngenHitta          # Hoppa Hitta-steget helt
)

$ErrorActionPreference = "Stop"

function Step($nr, $namn) {
    Write-Host ""
    Write-Host "--- Steg $nr: $namn ---" -ForegroundColor Cyan
}
function OK($msg)  { Write-Host "OK  $msg" -ForegroundColor Green }
function ERR($msg) { Write-Host "FEL $msg" -ForegroundColor Red; exit 1 }

# ── Hitta sökvägar ────────────────────────────────────────────────────────────

function Ask-Path($prompt, $default) {
    if ($default -and (Test-Path $default)) {
        Write-Host "$prompt" -NoNewline
        Write-Host " [Enter = $default]" -ForegroundColor DarkGray -NoNewline
        Write-Host ": " -NoNewline
        $inp = Read-Host
        if ($inp -eq "") { return $default }
        return $inp
    }
    Write-Host "${prompt}: " -NoNewline
    return Read-Host
}

Write-Host ""
Write-Host "=== Solar Scout Pipeline ===" -ForegroundColor Cyan

# Hämta mappar från användaren (bara om vi kör steg 1 eller 2)
if ($StartFran -le 2) {
    Write-Host ""
    Write-Host "Ange sökvägar till mapparna med energideklarationsfiler." -ForegroundColor Yellow
    Write-Host "(Tryck Enter för att hoppa ett steg om du redan har JSON-filen)"
    Write-Host ""

    # Försök hitta OneDrive automatiskt
    $oneDrive = (Get-ChildItem "$env:USERPROFILE" -Filter "OneDrive*" -Directory -ErrorAction SilentlyContinue | Select-Object -First 1).FullName
    if (-not $oneDrive) { $oneDrive = "$env:USERPROFILE\OneDrive" }

    $xlsmDefault = "$oneDrive\Enspecta Energideklarationer"
    $pdfDefault  = "$oneDrive\Enspecta Energideklarationer\PDF Energidek"

    $xlsmDir = Ask-Path "Mapp med XLSM-filer" $xlsmDefault
    $pdfDir  = Ask-Path "Mapp med PDF-filer " $pdfDefault
}

Write-Host ""

# ── Steg 1: XLSM → xlsm.json ─────────────────────────────────────────────────
if ($StartFran -le 1) {
    Step 1 "XLSM-filer → xlsm.json"
    if (-not $xlsmDir -or -not (Test-Path $xlsmDir)) {
        Write-Host "Hoppar (mapp saknas eller ej angiven)" -ForegroundColor DarkGray
    } else {
        node batchXlsm.mjs "$xlsmDir" xlsm.json
        if ($LASTEXITCODE -ne 0) { ERR "batchXlsm.mjs misslyckades" }
        OK "xlsm.json klar"
    }
}

# ── Steg 2: PDF → pdf.json ────────────────────────────────────────────────────
if ($StartFran -le 2) {
    Step 2 "PDF-filer → pdf.json"
    if (-not $pdfDir -or -not (Test-Path $pdfDir)) {
        Write-Host "Hoppar (mapp saknas eller ej angiven)" -ForegroundColor DarkGray
    } else {
        python extractEnergyPdf.py "$pdfDir" pdf.json
        if ($LASTEXITCODE -ne 0) { ERR "extractEnergyPdf.py misslyckades" }
        OK "pdf.json klar"
    }
}

# ── Steg 3: Merge → energy-data.json ─────────────────────────────────────────
if ($StartFran -le 3) {
    Step 3 "Slår ihop xlsm.json + pdf.json → energy-data.json"
    $inputs = @()
    if (Test-Path "xlsm.json") { $inputs += "xlsm.json" }
    if (Test-Path "pdf.json")  { $inputs += "pdf.json" }
    if ($inputs.Count -eq 0) { ERR "Varken xlsm.json eller pdf.json hittades" }
    python mergeEnergy.py @inputs energy-data.json
    if ($LASTEXITCODE -ne 0) { ERR "mergeEnergy.py misslyckades" }
    OK "energy-data.json klar"
}

# ── Steg 4: Hitta.se → kontaktuppgifter ──────────────────────────────────────
if ($StartFran -le 4 -and -not $IngenHitta) {
    Step 4 "Berika med kontaktuppgifter via Hitta.se"
    if (-not (Test-Path "energy-data.json")) { ERR "energy-data.json saknas" }

    $args4 = @("enrichContacts.py", "energy-data.json")
    if ($HittaLimit -gt 0)  { $args4 += "--limit"; $args4 += $HittaLimit }
    if ($HittaHeaded)        { $args4 += "--headed" }

    Write-Host "(Öppnar Chromium — kan ta en stund för alla poster)" -ForegroundColor DarkGray
    python @args4
    if ($LASTEXITCODE -ne 0) { ERR "enrichContacts.py misslyckades" }
    OK "Kontaktuppgifter inlagda i energy-data.json"
} elseif ($IngenHitta) {
    Write-Host ""
    Write-Host "Steg 4 hoppas (--IngenHitta)" -ForegroundColor DarkGray
}

# ── Steg 5: energy-list.xlsx (för FileMaker) ──────────────────────────────────
if ($StartFran -le 5) {
    Step 5 "Exporterar energy-list.xlsx (för FileMaker)"
    if (-not (Test-Path "energy-data.json")) { ERR "energy-data.json saknas" }
    python exportEnergyList.py energy-data.json energy-list.xlsx
    if ($LASTEXITCODE -ne 0) { ERR "exportEnergyList.py misslyckades" }
    OK "energy-list.xlsx klar"
}

# ── Steg 6: leads.xlsx (ringlista) ────────────────────────────────────────────
if ($StartFran -le 6) {
    Step 6 "Genererar leads.xlsx (ringlista)"
    if (-not (Test-Path "enspecta.tab")) { ERR "enspecta.tab saknas i projektmappen" }
    python makeLeads.py enspecta.tab leads.xlsx --energy energy-data.json
    if ($LASTEXITCODE -ne 0) { ERR "makeLeads.py misslyckades" }
    OK "leads.xlsx klar"
}

# ── Klar ─────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== Allt klart! ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "Filer skapade:" -ForegroundColor Yellow
if (Test-Path "energy-list.xlsx") { Write-Host "  energy-list.xlsx  — alla energidekl. med kontakter (FileMaker)" }
if (Test-Path "leads.xlsx")       { Write-Host "  leads.xlsx        — poängsorterad ringlista (David)" }
Write-Host ""
Write-Host "Tips:" -ForegroundColor DarkGray
Write-Host "  Hoppa Hitta-steget:  .\kor.ps1 -IngenHitta" -ForegroundColor DarkGray
Write-Host "  Starta om från steg 4:  .\kor.ps1 -StartFran 4" -ForegroundColor DarkGray
Write-Host "  Testa Hitta på 20 st:   .\kor.ps1 -StartFran 4 -HittaLimit 20 -HittaHeaded" -ForegroundColor DarkGray
Write-Host ""
