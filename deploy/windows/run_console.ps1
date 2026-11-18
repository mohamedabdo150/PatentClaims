# Run the converter in this console window, without NSSM (testing / troubleshooting).
# Uses the same environment as the Windows service. Stop with Ctrl+C.
#
#   powershell -ExecutionPolicy Bypass -File deploy\windows\run_console.ps1
#   powershell -ExecutionPolicy Bypass -File deploy\windows\run_console.ps1 -Port 50014 -TestPage
#
# If the service already listens on the port, stop it first (net stop PatentClaimsConverter)
# or pass another -Port.
param(
  [string]$InstallDir  = "C:\PatentConverter",          # folder created by install.ps1 (app\ and venv\)
  [string]$DataDir     = "C:\PatentConverter\data",
  [int]   $Port        = 50013,
  [string]$SofficePath = "C:\Program Files\LibreOffice\program\soffice.com",
  [string]$PopplerBin  = "C:\Drawing_Detection\poppler-23.07.0\Library\bin",
  [string]$ModelPath   = "C:\Drawing_Detection\models\best_model.h5",
  [switch]$TestPage,                                    # serve /test/ckeditor4
  [switch]$NoPdfRoutes
)
$ErrorActionPreference = "Stop"
$App = Join-Path $InstallDir "app"
$Py  = Join-Path $InstallDir "venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { throw "Python venv not found at $Py (run install.ps1 first, or pass -InstallDir)" }
if (-not (Test-Path (Join-Path $App "src\patent_converter"))) { throw "Application not found under $App" }
New-Item -ItemType Directory -Force -Path $DataDir, (Join-Path $DataDir "spool") | Out-Null

$env:PYTHONPATH = Join-Path $App "src"
$env:PYTHONUTF8 = "1"
$env:PATENT_CONFIG_FILE = Join-Path $App "config\app.windows.yaml"
$env:TMP = Join-Path $DataDir "spool"; $env:TEMP = $env:TMP
$env:PATENT__STORAGE__TEMPORARY_DIRECTORY = $DataDir
$env:PATENT__CONVERSION__SOFFICE_PATH = $SofficePath
$env:PATENT__CONVERSION__PDFTOCAIRO_PATH = Join-Path $PopplerBin "pdftocairo.exe"
$env:PATENT__PDF__MODEL_PATH = $ModelPath
$env:PATENT__PDF__POPPLER_PATH = $PopplerBin
$env:PATENT__LEGACY__PORT = "$Port"
$env:TF_USE_LEGACY_KERAS = "1"
$env:PATENT__SECURITY__AUTH_ENABLED = "false"
if ($TestPage)    { $env:PATENT__TEST_PAGE__ENABLED = "true" }
if ($NoPdfRoutes) { $env:PATENT__PDF__ENABLED = "false" }
# Human-readable log lines in the console instead of JSON.
$env:PATENT__LOGGING__JSON_FORMAT = "false"

Write-Host "Patent Claims Converter on http://localhost:$Port  (Ctrl+C to stop)" -ForegroundColor Cyan
if ($TestPage) { Write-Host "Test page: http://localhost:$Port/test/ckeditor4" -ForegroundColor Cyan }
Set-Location $App
& $Py -m uvicorn patent_converter.main:create_app --factory --host 0.0.0.0 --port $Port --workers 1 `
  --timeout-keep-alive 75 --timeout-graceful-shutdown 10 --no-server-header
