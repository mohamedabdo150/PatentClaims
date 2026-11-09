<#
.SYNOPSIS
  Installs the Patent Claims Converter as a Windows service (NSSM) on port 50013.

.DESCRIPTION
  Run from an elevated PowerShell in the repository root:
    powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1 -AllowedClients 10.0.0.15,10.0.0.16

  The old service must be stopped or moved to another port first: the script refuses to
  continue while the port is in use. Rollback: deploy\windows\uninstall.ps1, then start the
  old service again.
#>
[CmdletBinding()]
param(
  [string]$InstallDir     = "C:\PatentConverter",
  [string]$DataDir        = "C:\PatentConverter\data",
  [int]   $Port           = 50013,
  [string]$ServiceName    = "PatentClaimsConverter",
  [string]$ServiceAccount = "NT AUTHORITY\LocalService",
  [string]$PythonExe      = "",
  [string]$NssmPath       = "C:\Drawing_Detection\nssm.exe",
  [string]$SofficePath    = "C:\Program Files\LibreOffice\program\soffice.com",
  [string]$PopplerBin     = "C:\Drawing_Detection\poppler-23.07.0\Library\bin",
  [string]$ModelPath      = "C:\Drawing_Detection\models\best_model.h5",
  [string[]]$AllowedClients = @(),
  [string]$ApiKey         = "",
  [int]   $Workers        = 1,
  [switch]$NoPdfRoutes,
  [switch]$SkipFirewall
)
$ErrorActionPreference = "Stop"
function Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Fail($m) { Write-Host "ERROR: $m" -ForegroundColor Red; exit 1 }

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { Fail "run this script as Administrator" }
$Repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

Step "Checking port $Port"
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
  Fail "port $Port is in use. Stop the old service or move it to another port first (e.g. API_PORT=50015)."
}

Step "Checking prerequisites"
if (-not $PythonExe) {
  $py = Get-Command py -ErrorAction SilentlyContinue
  if ($py) { $PythonExe = (& py -3.12 -c "import sys; print(sys.executable)").Trim() }
  else { $PythonExe = (Get-Command python -ErrorAction Stop).Source }
}
if ($PythonExe -match "LibreOffice") { Fail "the selected Python ($PythonExe) is LibreOffice's bundled interpreter; pass -PythonExe <python 3.12>" }
$ver = (& $PythonExe -c "import sys; print('%d.%d' % sys.version_info[:2])").Trim()
if ($ver -ne "3.12") { Fail "Python 3.12 required (found $ver at $PythonExe)" }
if (-not (Test-Path $NssmPath)) { Fail "nssm.exe not found at $NssmPath (use -NssmPath)" }
if (-not (Test-Path $SofficePath)) { Fail "LibreOffice not found at $SofficePath (install LibreOffice 24.x, or use -SofficePath)" }
$Pdftocairo = Join-Path $PopplerBin "pdftocairo.exe"
if (-not (Test-Path $Pdftocairo)) { Fail "pdftocairo.exe not found in $PopplerBin (use -PopplerBin)" }
if (-not $NoPdfRoutes -and -not (Test-Path $ModelPath)) {
  Write-Warning "model not found at ${ModelPath} - /drawing_detection will answer 'Model not loaded' until it is provided"
}

Step "Copying application to $InstallDir\app"
$App = Join-Path $InstallDir "app"
New-Item -ItemType Directory -Force -Path $App, (Join-Path $InstallDir "logs") | Out-Null
foreach ($item in @("src", "config")) {
  robocopy (Join-Path $Repo $item) (Join-Path $App $item) /MIR /NFL /NDL /NJH /NJS /XD __pycache__ | Out-Null
  if ($LASTEXITCODE -ge 8) { Fail "copy of $item failed" }
}
Copy-Item (Join-Path $Repo "requirements*.txt") $App -Force

Step "Creating virtual environment and installing dependencies"
$Venv = Join-Path $InstallDir "venv"
if (-not (Test-Path (Join-Path $Venv "Scripts\python.exe"))) { & $PythonExe -m venv $Venv }
$VPy = Join-Path $Venv "Scripts\python.exe"
& $VPy -m pip install --upgrade pip -q
& $VPy -m pip install -q -r (Join-Path $App "requirements-windows.txt")
if ($LASTEXITCODE -ne 0) { Fail "pip install failed" }
if (-not $NoPdfRoutes) {
  & $VPy -m pip install -q -r (Join-Path $App "requirements-pdf.txt")
  if ($LASTEXITCODE -ne 0) { Fail "pip install of PDF dependencies failed" }
}

Step "Creating data directory $DataDir (access: SYSTEM, Administrators, $ServiceAccount)"
New-Item -ItemType Directory -Force -Path $DataDir, (Join-Path $DataDir "spool") | Out-Null
& icacls $DataDir /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" "${ServiceAccount}:(OI)(CI)M" | Out-Null
& icacls (Join-Path $InstallDir "logs") /grant "${ServiceAccount}:(OI)(CI)M" | Out-Null

Step "Registering service $ServiceName"
$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) { & $NssmPath stop $ServiceName | Out-Null; & $NssmPath remove $ServiceName confirm | Out-Null }
$uvArgs = "-m uvicorn patent_converter.main:create_app --factory --host 0.0.0.0 --port $Port --workers $Workers --timeout-keep-alive 75 --no-server-header"
& $NssmPath install $ServiceName $VPy $uvArgs | Out-Null
& $NssmPath set $ServiceName AppDirectory $App | Out-Null
& $NssmPath set $ServiceName DisplayName "Patent Claims Converter" | Out-Null
& $NssmPath set $ServiceName Description "Bilingual patent claims DOCX-to-HTML (legacy routes on port $Port)" | Out-Null
& $NssmPath set $ServiceName Start SERVICE_AUTO_START | Out-Null
& $NssmPath set $ServiceName ObjectName $ServiceAccount | Out-Null
$env_lines = @(
  "PYTHONPATH=$App\src",
  "PYTHONUTF8=1",
  "PATENT_CONFIG_FILE=$App\config\app.windows.yaml",
  "TMP=$DataDir\spool", "TEMP=$DataDir\spool",
  "PATENT__STORAGE__TEMPORARY_DIRECTORY=$DataDir",
  "PATENT__CONVERSION__SOFFICE_PATH=$SofficePath",
  "PATENT__CONVERSION__PDFTOCAIRO_PATH=$Pdftocairo",
  "PATENT__PDF__MODEL_PATH=$ModelPath",
  "PATENT__PDF__POPPLER_PATH=$PopplerBin",
  "PATENT__LEGACY__PORT=$Port",
  "TF_USE_LEGACY_KERAS=1"
)
if ($NoPdfRoutes) { $env_lines += "PATENT__PDF__ENABLED=false" }
if ($ApiKey) {
  $sha = [System.Security.Cryptography.SHA256]::Create()
  $hash = ($sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($ApiKey)) | ForEach-Object { $_.ToString("x2") }) -join ""
  $env_lines += "PATENT__SECURITY__API_KEY_SHA256=$hash"
} else {
  Write-Warning "no -ApiKey: /api/v1 endpoints run without authentication (legacy routes never required one)"
  $env_lines += "PATENT__SECURITY__AUTH_ENABLED=false"
}
& $NssmPath set $ServiceName AppEnvironmentExtra $env_lines | Out-Null
$logs = Join-Path $InstallDir "logs"
& $NssmPath set $ServiceName AppStdout (Join-Path $logs "service.log") | Out-Null
& $NssmPath set $ServiceName AppStderr (Join-Path $logs "service.log") | Out-Null
& $NssmPath set $ServiceName AppRotateFiles 1 | Out-Null
& $NssmPath set $ServiceName AppRotateOnline 1 | Out-Null
& $NssmPath set $ServiceName AppRotateBytes 52428800 | Out-Null
& $NssmPath set $ServiceName AppStopMethodConsole 15000 | Out-Null
& $NssmPath set $ServiceName AppExit Default Restart | Out-Null

if (-not $SkipFirewall) {
  Step "Firewall rule for port $Port"
  Get-NetFirewallRule -DisplayName "$ServiceName $Port" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
  if ($AllowedClients.Count -gt 0) {
    New-NetFirewallRule -DisplayName "$ServiceName $Port" -Direction Inbound -Protocol TCP -LocalPort $Port `
      -RemoteAddress $AllowedClients -Action Allow | Out-Null
  } else {
    Write-Warning "no -AllowedClients: add a firewall rule restricting port $Port to the OutSystems servers"
  }
}

Step "Starting service"
& $NssmPath start $ServiceName | Out-Null
$ok = $false
for ($i = 0; $i -lt 60; $i++) {
  Start-Sleep -Seconds 2
  try { $h = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 5; if ($h.status -eq "OK") { $ok = $true; break } } catch {}
}
if (-not $ok) { Fail "service did not become healthy; see $logs\service.log" }
Write-Host ("Healthy: " + ($h | ConvertTo-Json -Compress)) -ForegroundColor Green
