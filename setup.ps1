# setup.ps1 — Installerar alla beroenden för solar-scout pipeline
# Kör en gång: .\setup.ps1
# Kräver: Python 3.10+ och Node.js 18+ installerade

Write-Host ""
Write-Host "=== Solar Scout — Setup ===" -ForegroundColor Cyan
Write-Host ""

# --- Kontrollera Python ---
$pyver = python --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "FEL: Python hittades inte. Installera från https://python.org" -ForegroundColor Red
    exit 1
}
Write-Host "OK  $pyver" -ForegroundColor Green

# --- Kontrollera Node.js ---
$nodever = node --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "FEL: Node.js hittades inte. Installera från https://nodejs.org" -ForegroundColor Red
    exit 1
}
Write-Host "OK  Node $nodever" -ForegroundColor Green

# --- Python-paket ---
Write-Host ""
Write-Host "Installerar Python-paket..." -ForegroundColor Yellow
pip install -r requirements.txt --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host "FEL: pip install misslyckades" -ForegroundColor Red
    exit 1
}
Write-Host "OK  Python-paket installerade" -ForegroundColor Green

# --- Playwright Chromium ---
Write-Host ""
Write-Host "Installerar Playwright Chromium (kan ta 1-2 min)..." -ForegroundColor Yellow
playwright install chromium
if ($LASTEXITCODE -ne 0) {
    Write-Host "FEL: playwright install chromium misslyckades" -ForegroundColor Red
    exit 1
}
Write-Host "OK  Playwright Chromium installerat" -ForegroundColor Green

# --- Node-moduler ---
Write-Host ""
Write-Host "Kontrollerar Node-moduler..." -ForegroundColor Yellow
if (-not (Test-Path "node_modules")) {
    npm install --silent 2>&1 | Out-Null
}
Write-Host "OK  Node-moduler klara" -ForegroundColor Green

Write-Host ""
Write-Host "=== Setup klar! Kör nu: .\kor.ps1 ===" -ForegroundColor Cyan
Write-Host ""
