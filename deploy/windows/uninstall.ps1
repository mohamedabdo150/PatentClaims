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
if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
  & $NssmPath stop $ServiceName | Out-Null
  & $NssmPath remove $ServiceName confirm | Out-Null
  Write-Host "service $ServiceName removed"
}
Get-NetFirewallRule -DisplayName "$ServiceName $Port" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
if ($RemoveFiles -and (Test-Path $InstallDir)) { Remove-Item -Recurse -Force $InstallDir; Write-Host "$InstallDir removed" }
Write-Host "Port $Port is free: the old service can be started on it again."
