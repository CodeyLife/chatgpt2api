#Requires -Version 5.1
<#
.SYNOPSIS
    Update chatgpt2api on Windows server from a zip.
.DESCRIPTION
    - Stops the service
    - Extracts the zip to the project dir (overwrites code, preserves data/config/bin)
    - Runs uv sync if dependencies changed
    - Restarts the service
.NOTES
    Usage (Admin PowerShell):
      powershell -ExecutionPolicy Bypass -File scripts\windows\update.ps1 -ZipPath C:\chatgpt2api.zip
    If -ZipPath omitted, looks for chatgpt2api.zip next to the project root.
#>
param(
    [string]$ZipPath
)

$ErrorActionPreference = 'Stop'

# ====== Paths ======
$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) { $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $ScriptDir)
Set-Location $ProjectRoot
$BinDir     = Join-Path $ProjectRoot "scripts\windows\bin"
$UvExe      = Join-Path $BinDir "uv.exe"
$NssmExe    = Join-Path $BinDir "nssm.exe"
$DataDir    = Join-Path $ProjectRoot "data"
$ConfigFile = Join-Path $ProjectRoot "config.json"
$ServiceName = if ($env:CHATGPT2API_SERVICE_NAME) { $env:CHATGPT2API_SERVICE_NAME } else { "ChatGPT2API" }

function Write-Step($m){ Write-Host "`n[*] $m" -ForegroundColor Cyan }
function Write-Ok($m) { Write-Host "[OK] $m" -ForegroundColor Green }
function Write-Warn($m){ Write-Host "[!] $m" -ForegroundColor Yellow }
function Die($m)      { Write-Host "[X] $m" -ForegroundColor Red; exit 1 }

# Run nssm and suppress stderr (PowerShell Stop EAP would turn nssm stderr into fatal errors)
function Invoke-Nssm {
    $argsStr = $args -join ' '
    cmd /c "`"$NssmExe`" $argsStr >nul 2>&1"
}

# ====== 1. Admin check ======
Write-Step "Checking admin privileges..."
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Die "Admin privileges required. Right-click PowerShell -> Run as administrator."
}
Write-Ok "Admin confirmed"

# ====== 2. Locate zip ======
if (-not $ZipPath) {
    $candidate = Join-Path (Split-Path -Parent $ProjectRoot) "chatgpt2api.zip"
    if (Test-Path $candidate) { $ZipPath = $candidate }
}
if (-not $ZipPath -or -not (Test-Path $ZipPath)) {
    Die "Zip not found. Usage: update.ps1 -ZipPath C:\path\to\chatgpt2api.zip"
}
Write-Ok "Zip: $ZipPath"

# ====== 3. Stop service ======
Write-Step "Stopping service $ServiceName ..."
Invoke-Nssm stop $ServiceName
Start-Sleep -Seconds 2
Write-Ok "Service stopped"

# ====== 4. Extract zip to temp, then overlay onto project root ======
Write-Step "Extracting zip ..."
$extractDir = Join-Path $env:TEMP "chatgpt2api-update-$(Get-Random)"
if (Test-Path $extractDir) { Remove-Item $extractDir -Recurse -Force }
Expand-Archive -Path $ZipPath -DestinationPath $extractDir -Force

# Determine if zip contains a top-level folder or files directly
$topItems = Get-ChildItem $extractDir
if ($topItems.Count -eq 1 -and $topItems[0].PSIsContainer) {
    $srcRoot = $topItems[0].FullName
} else {
    $srcRoot = $extractDir
}

# Overlay: copy everything from zip, but preserve local config.json / data / bin
$preserveNames = @("config.json", "data")
foreach ($item in Get-ChildItem $srcRoot -Force) {
    $dest = Join-Path $ProjectRoot $item.Name
    if ($preserveNames -contains $item.Name) {
        Write-Host "    skip (preserved): $($item.Name)" -ForegroundColor DarkGray
        continue
    }
    # scripts\windows\bin should be preserved too (uv.exe / nssm.exe already downloaded)
    if ($item.Name -eq "scripts" -and (Test-Path $dest)) {
        # Merge: copy files from zip into existing scripts, but keep bin/
        foreach ($sub in Get-ChildItem $item.FullName -Recurse -Force) {
            $rel = $sub.FullName.Substring($item.FullName.Length + 1)
            $target = Join-Path $dest $rel
            if ($rel -like "bin\*" -and (Test-Path $target)) {
                Write-Host "    skip (preserved): scripts\$rel" -ForegroundColor DarkGray
                continue
            }
            $targetParent = Split-Path -Parent $target
            if (-not (Test-Path $targetParent)) { New-Item -ItemType Directory -Path $targetParent -Force | Out-Null }
            if ($sub.PSIsContainer) {
                New-Item -ItemType Directory -Path $target -Force | Out-Null
            } else {
                Copy-Item $sub.FullName $target -Force
            }
        }
        continue
    }
    if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
    Copy-Item $item.FullName $dest -Recurse -Force
}
Remove-Item $extractDir -Recurse -Force
Write-Ok "Code updated"

# ====== 6. Sync dependencies (in case pyproject.toml/uv.lock changed) ======
if (Test-Path $UvExe) {
    Write-Step "Syncing Python dependencies ..."
    & $UvExe sync --frozen --no-dev
    if ($LASTEXITCODE -ne 0) { Die "uv sync failed" }
    Write-Ok "Dependencies synced"
} else {
    Write-Warn "uv.exe not found at $UvExe, skipping deps sync"
}

# ====== 7. Restart service ======
Write-Step "Starting service ..."
Invoke-Nssm start $ServiceName
Start-Sleep -Seconds 3

# ====== 8. Verify ======
# Read actual port from nssm service environment (set during deploy)
$port = $env:CHATGPT2API_PORT
if (-not $port) {
    $envOut = cmd /c "`"$NssmExe`" dump $ServiceName" 2>&1
    $m = [regex]::Match($envOut, 'CHATGPT2API_PORT=(\d+)')
    if ($m.Success) { $port = $m.Groups[1].Value }
}
if (-not $port) { $port = "8000" }
Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Ok "Update complete!"
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Verify:    http://127.0.0.1:$port/" -ForegroundColor White
Write-Host "  Logs:      $DataDir\service.err.log" -ForegroundColor Gray
Write-Host ""

# Quick health probe
try {
    $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$port/health" -UseBasicParsing -TimeoutSec 5
    Write-Ok "Health check: $($resp.StatusCode)"
} catch {
    Write-Warn "Health check failed, check service.err.log"
}
