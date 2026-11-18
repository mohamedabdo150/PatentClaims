# Run the converter in this console window, without NSSM (testing / troubleshooting).
# Stop with Ctrl+C.
#
# Two layouts:
#  * Repository checkout (default when no installed copy is found): runs from the folder
#    that contains deploy\windows\run_console.ps1. On first run it creates <repo>\.venv from
#    Python 3.12 and installs the requirements.
#      powershell -ExecutionPolicy Bypass -File deploy\windows\run_console.ps1 -PythonExe "C:\Program Files\Python312\python.exe"
#  * Installed by install.ps1: pass -InstallDir C:\PatentConverter (uses its app\ and venv\).
#
# Options: -Port 50014 (if the service already uses 50013), -TestPage (/test/ckeditor4),
#          -NoPdfRoutes (skip TensorFlow / PDF routes).
param(
  [string]$InstallDir  = "",                            # folder created by install.ps1; empty = this checkout
  [string]$PythonExe   = "",                            # Python 3.12, used only to create the venv
  [string]$DataDir     = "",                            # default: <install>\data or <repo>\data
  [int]   $Port        = 50013,
  [string]$SofficePath = "C:\Program Files\LibreOffice\program\soffice.com",
  [string]$PopplerBin  = "C:\Drawing_Detection\poppler-23.07.0\Library\bin",
  [string]$ModelPath   = "C:\Drawing_Detection\models\best_model.h5",
  [switch]$TestPage,
  [switch]$NoPdfRoutes
)
$ErrorActionPreference = "Stop"
function Fail($m) { Write-Host "ERROR: $m" -ForegroundColor Red; exit 1 }

# ------------------------------------------------------------------ layout
if ($InstallDir) {
  $App  = Join-Path $InstallDir "app"
  $Venv = Join-Path $InstallDir "venv"
  if (-not $DataDir) { $DataDir = Join-Path $InstallDir "data" }
} else {
  $App  = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
  $Venv = Join-Path $App ".venv"
  if (-not $DataDir) { $DataDir = Join-Path $App "data" }
}
if (-not (Test-Path (Join-Path $App "src\patent_converter"))) { Fail "application not found under $App" }
$Py = Join-Path $Venv "Scripts\python.exe"

# ------------------------------------------------------------------ venv (first run only)
if (-not (Test-Path $Py)) {
  if ($InstallDir) { Fail "venv not found at $Venv (run install.ps1 first, or omit -InstallDir to run from this checkout)" }
  if (-not $PythonExe) {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) { $PythonExe = (& py -3.12 -c "import sys; print(sys.executable)" 2>$null) }
    if (-not $PythonExe) { Fail 'Python 3.12 not found: pass -PythonExe "C:\Program Files\Python312\python.exe"' }
    $PythonExe = $PythonExe.Trim()
  }
  if (-not (Test-Path $PythonExe)) { Fail "Python not found at $PythonExe" }
  if ($PythonExe -match "LibreOffice") { Fail "$PythonExe is LibreOffice's bundled Python; pass Python 3.12" }
  $ver = (& $PythonExe -c "import sys; print('%d.%d' % sys.version_info[:2])").Trim()
  if ($ver -ne "3.12") { Fail "Python 3.12 required (found $ver at $PythonExe)" }
  Write-Host "==> Creating $Venv and installing requirements (first run only, a few minutes)" -ForegroundColor Cyan
  & $PythonExe -m venv $Venv
  if ($LASTEXITCODE -ne 0) { Fail "venv creation failed" }
  & $Py -m pip install --upgrade pip -q
  & $Py -m pip install -q -r (Join-Path $App "requirements-windows.txt")
  if ($LASTEXITCODE -ne 0) { Fail "pip install failed" }
  if (-not $NoPdfRoutes) {
    & $Py -m pip install -q -r (Join-Path $App "requirements-pdf.txt")
    if ($LASTEXITCODE -ne 0) { Fail "pip install of PDF dependencies failed (or run with -NoPdfRoutes)" }
  }
}

# ------------------------------------------------------------------ checks
if (-not (Test-Path $SofficePath)) { Fail "LibreOffice not found at $SofficePath (pass -SofficePath)" }
$Pdftocairo = Join-Path $PopplerBin "pdftocairo.exe"
if (-not (Test-Path $Pdftocairo)) { Fail "pdftocairo.exe not found in $PopplerBin (pass -PopplerBin)" }
if (-not $NoPdfRoutes -and -not (Test-Path $ModelPath)) {
  Write-Warning "model not found at ${ModelPath}: /drawing_detection will answer 'Model not loaded' (pass -ModelPath)"
}
New-Item -ItemType Directory -Force -Path $DataDir, (Join-Path $DataDir "spool") | Out-Null

# ------------------------------------------------------------------ environment (same as the service)
$env:PYTHONPATH = Join-Path $App "src"
$env:PYTHONUTF8 = "1"
$env:PATENT_CONFIG_FILE = Join-Path $App "config\app.windows.yaml"
$env:TMP = Join-Path $DataDir "spool"; $env:TEMP = $env:TMP
$env:PATENT__STORAGE__TEMPORARY_DIRECTORY = $DataDir
$env:PATENT__CONVERSION__SOFFICE_PATH = $SofficePath
$env:PATENT__CONVERSION__PDFTOCAIRO_PATH = $Pdftocairo
$env:PATENT__PDF__MODEL_PATH = $ModelPath
$env:PATENT__PDF__POPPLER_PATH = $PopplerBin
$env:PATENT__LEGACY__PORT = "$Port"
$env:TF_USE_LEGACY_KERAS = "1"
$env:PATENT__SECURITY__AUTH_ENABLED = "false"           # console testing: /api/v1 without API key
$env:PATENT__LOGGING__JSON_FORMAT = "false"             # readable console logs
if ($TestPage)    { $env:PATENT__TEST_PAGE__ENABLED = "true" }
if ($NoPdfRoutes) { $env:PATENT__PDF__ENABLED = "false" }

Write-Host "Patent Claims Converter from $App on http://localhost:$Port  (Ctrl+C to stop)" -ForegroundColor Cyan
if ($TestPage) { Write-Host "Test page: http://localhost:$Port/test/ckeditor4" -ForegroundColor Cyan }
Set-Location $App
& $Py -m uvicorn patent_converter.main:create_app --factory --host 0.0.0.0 --port $Port --workers 1 `
  --timeout-keep-alive 75 --timeout-graceful-shutdown 10 --no-server-header
