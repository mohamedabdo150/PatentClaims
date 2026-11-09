# Windows Server deployment

## Recommendation: native install (Windows service), not Docker
* The project's Docker image is a **Linux** container. On Windows Server it would need
  WSL2/Hyper-V Linux containers, which are not a supported production setup there, and
  Docker Desktop is not licensed for servers.
* Rebuilding it as a Windows container (Server Core + LibreOffice) would produce a
  ~5 GB image with no functional benefit.
* The team already runs the previous service as an NSSM Windows service. The new
  service is installed the same way and reuses `nssm.exe`, poppler and the model in
  `C:\Drawing_Detection`.
* If a Linux server/VM becomes available, the Docker image ([deployment.md](deployment.md))
  remains the simplest option there.

Windows-specific behaviour (verified by the `windows` GitHub Actions workflow):
LibreOffice/poppler run in a **Job Object** (whole process tree terminated on timeout,
memory-limited, killed when the job closes); UTF-8 everywhere; data directory protected
by NTFS ACLs.

## Prerequisites
| Component | Version / location |
|---|---|
| Windows Server | 2019 / 2022 |
| Python | 3.12 (python.org installer, "for all users") |
| LibreOffice | 24.x, default `C:\Program Files\LibreOffice` |
| poppler | existing `C:\Drawing_Detection\poppler-23.07.0\Library\bin` |
| NSSM | existing `C:\Drawing_Detection\nssm.exe` |
| Drawing model | existing `C:\Drawing_Detection\models\best_model.h5` |
| Repository | checkout or copy of `PatentClaims` on the server |

## Install / cut-over
1. **Stop the old service** (or move it to another port, e.g. set `API_PORT=50015`):
   ```powershell
   net stop DrawingDetectionService
   ```
   The installer refuses to continue while port 50013 is in use.
2. **Install** (elevated PowerShell, repository root):
   ```powershell
   powershell -ExecutionPolicy Bypass -File deploy\windows\install.ps1 -AllowedClients 10.0.0.15,10.0.0.16 -ApiKey "<key for /api/v1>"
   ```
   Parameters: `-InstallDir` (default `C:\PatentConverter`), `-DataDir`, `-Port` (50013),
   `-ServiceAccount` (default `NT AUTHORITY\LocalService`), `-SofficePath`, `-PopplerBin`,
   `-ModelPath`, `-NssmPath`, `-Workers`, `-NoPdfRoutes`, `-SkipFirewall`.
   The script copies the application, creates a virtual environment with the pinned
   dependencies, protects the data directory, registers the service
   `PatentClaimsConverter` (auto start, restart on failure, rotated log
   `C:\PatentConverter\logs\service.log`), adds a firewall rule for the OutSystems
   servers and waits until `/health` answers `OK`.
3. **Smoke test** all routes:
   ```powershell
   python scripts\generate_fixtures.py --out fx
   python -c "from PIL import Image; Image.new('RGB',(64,48)).save('fx/probe.png')"
   python deploy\windows\smoke_test.py --port 50013 --fixtures fx --expect-model
   ```
4. **Parity check of `/drawing_detection`** (optional, recommended): start the old service
   on another port and send the same PDFs to both. The results must be identical (same
   model, same preprocessing).

## Rollback
```powershell
powershell -ExecutionPolicy Bypass -File deploy\windows\uninstall.ps1
net start DrawingDetectionService
```

## Configuration
`C:\PatentConverter\app\config\app.windows.yaml`, overridden by the service environment
(`nssm edit PatentClaimsConverter` → Environment). Important keys:
`processing.max_concurrent_jobs_per_worker` (3), `processing.queue_wait_seconds` (600),
`conversion.libreoffice_parallelism` (4; raise on 16 vCPU), `output.max_response_size_mb`,
`legacy.require_api_key`.

## Operations
* Logs: `C:\PatentConverter\logs\service.log` (JSON lines, rotated at 50 MB; no patent text).
* Metrics: `http://<server>:50013/metrics` (Prometheus).
* Upgrade: pull the new version and run `install.ps1` again (it replaces the service; data is kept).
