<#
.SYNOPSIS
  Stops and removes the Patent Claims Converter service (rollback: then start the old service on 50013).
#>
[CmdletBinding()]
param(
  [string]$ServiceName = "PatentClaimsConverter",
  [string]$NssmPath    = "C:\Drawing_Detection\nssm.exe",
  [string]$InstallDir  = "C:\PatentConverter",
  [int]   $Port        = 50013,
  [switch]$RemoveFiles
)
$ErrorActionPreference = "Stop"
function Stop-ServiceTree([string]$Name) {
  # Stop via the Service Control Manager; never wait forever (kill the process tree after 60 s).
  $svc = Get-CimInstance Win32_Service -Filter "Name='$Name'" -ErrorAction SilentlyContinue
  if (-not $svc) { return }
  $svcPid = $svc.ProcessId
  if ($svc.State -ne "Stopped") {
    $job = Start-Job { param($n) Stop-Service -Name $n -Force -ErrorAction SilentlyContinue } -ArgumentList $Name
    if (-not (Wait-Job $job -Timeout 60)) { Write-Warning "service did not stop within 60 s; terminating its process tree" }
    Remove-Job $job -Force -ErrorAction SilentlyContinue
  }
  if ($svcPid -and (Get-Process -Id $svcPid -ErrorAction SilentlyContinue)) { & taskkill /T /F /PID $svcPid | Out-Null }
}
function Remove-ServiceEntry([string]$Name) {
  Stop-ServiceTree $Name
  if (Get-Service -Name $Name -ErrorAction SilentlyContinue) { & sc.exe delete $Name | Out-Null }
  for ($i = 0; $i -lt 30 -and (Get-Service -Name $Name -ErrorAction SilentlyContinue); $i++) { Start-Sleep -Seconds 1 }
}

if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
  Remove-ServiceEntry $ServiceName
  Write-Host "service $ServiceName removed"
}
Get-NetFirewallRule -DisplayName "$ServiceName $Port" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
if ($RemoveFiles -and (Test-Path $InstallDir)) { Remove-Item -Recurse -Force $InstallDir; Write-Host "$InstallDir removed" }
Write-Host "Port $Port is free: the old service can be started on it again."
