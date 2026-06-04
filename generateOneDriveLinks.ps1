# generateOneDriveLinks.ps1
# Skapar "Anyone with link"-delningslänkar för alla XLSM/PDF i energy-data.json
# och skriver tillbaka filsökvägarna + OneDrive-länkarna till energy-data.json
#
# Kräver: Microsoft.Graph PowerShell-modul
#   Install-Module Microsoft.Graph -Scope CurrentUser
#
# Kör:
#   .\generateOneDriveLinks.ps1 -EnergyJson "energy-data.json"

param(
    [string]$EnergyJson = "energy-data.json"
)

# Enklast: generera bara file://-länkar till lokala OneDrive-sökvägar
# (Fungerar för alla som har OneDrive synkroniserat på sin dator)

$data = Get-Content $EnergyJson -Encoding UTF8 | ConvertFrom-Json -AsHashtable

$updated = 0
foreach ($key in @($data.Keys)) {
    $rec = $data[$key]
    $sf = $rec['source_file']
    if ($sf -and (Test-Path $sf)) {
        # Generera file:// URI som Excel kan öppna direkt
        $uri = "file:///" + $sf.Replace('\', '/').Replace(' ', '%20')
        $data[$key]['source_file'] = $uri
        $updated++
    }
}

$data | ConvertTo-Json -Depth 10 | Set-Content $EnergyJson -Encoding UTF8
Write-Host "Uppdaterade $updated poster med file://-länkar i $EnergyJson"
Write-Host ""
Write-Host "Tips: För riktiga OneDrive-delningslänkar (Anyone with link):"
Write-Host "  1. Installera: Install-Module Microsoft.Graph -Scope CurrentUser"
Write-Host "  2. Kör: Connect-MgGraph -Scopes 'Files.ReadWrite'"
Write-Host "  3. Använd: New-MgDriveItemLink för varje fil"
