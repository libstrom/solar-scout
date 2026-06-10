# Enspecta Lead Machine -- nattjobb: trana -> skorda -> gradera.
# Schemalagd 03:00 via Schemalaggaren (task: EnspectaLeadMachine).
# Loggar till data\night_run.log. ASCII-only har: PS 5.1 laser
# BOM-losa filer som ANSI och forvanskar annars svenska tecken.
$ErrorActionPreference = 'Continue'
Set-Location $PSScriptRoot
$log = Join-Path $PSScriptRoot 'data\night_run.log'

"=== Nattjobb start $(Get-Date -Format 'yyyy-MM-dd HH:mm') ===" | Add-Content $log
python train.py *>> $log
python harvester_lm.py --max-buildings 200 *>> $log
python prescreen.py *>> $log
python train.py *>> $log
"=== Nattjobb klart $(Get-Date -Format 'HH:mm') ===" | Add-Content $log
