#Requires -Version 5.1
<#
.SYNOPSIS
    ChatGPT2API Windows one-click deploy script
.DESCRIPTION
    Auto: download uv + nssm -> sync Python deps -> create config.json -> register Windows service -> start
.NOTES
    Usage (Admin PowerShell):
      powershell -ExecutionPolicy Bypass -File scripts\windows\deploy.ps1
    Uninstall:
      powershell -ExecutionPolicy Bypass -File scripts\windows\deploy.ps1 -Uninstall
    Custom port:
      $env:CHATGPT2API_PORT=9000; powershell -ExecutionPolicy Bypass -File scripts\windows\deploy.ps1
#>
param(
    [switch]$Uninstall
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
$RunBat     = Join-Path $ProjectRoot "scripts\windows\run.bat"
$DataDir    = Join-Path $ProjectRoot "data"
$ConfigFile = Join-Path $ProjectRoot "config.json"
$ServiceName = if ($env:CHATGPT2API_SERVICE_NAME) { $env:CHATGPT2API_SERVICE_NAME } else { "ChatGPT2API" }
$Port        = if ($env:CHATGPT2API_PORT) { $env:CHATGPT2API_PORT } else { "9188" }

function Write-Step($m){ Write-Host "`n[*] $m" -ForegroundColor Cyan }
function Write-Ok($m) { Write-Host "[OK] $m" -ForegroundColor Green }
function Write-Warn($m){ Write-Host "[!] $m" -ForegroundColor Yellow }
function Die($m)       { Write-Host "[X] $m" -ForegroundColor Red; exit 1 }

# Run nssm and suppress all output (nssm writes normal messages to stderr,
# which PowerShell's Stop EAP would turn into terminating errors).
function Invoke-Nssm {
    $argsStr = $args -join ' '
    cmd /c "`"$NssmExe`" $argsStr >nul 2>&1"
}

# ====== Uninstall branch ======
if ($Uninstall) {
    Write-Step "Uninstalling service $ServiceName ..."
    if (Test-Path $NssmExe) {
        Invoke-Nssm stop $ServiceName
        Invoke-Nssm remove $ServiceName confirm
        Write-Ok "Service removed"
    } else {
        Die "nssm.exe not found. Remove service manually via services.msc"
    }
    Write-Host "`nUninstall done. Code and data directory not removed." -ForegroundColor Green
    exit 0
}

# ====== 1. Admin check ======
Write-Step "Checking admin privileges..."
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Die "Admin privileges required. Right-click PowerShell -> Run as administrator."
}
Write-Ok "Admin confirmed"

# ====== 2. Prepare directories ======
if (-not (Test-Path $BinDir))  { New-Item -ItemType Directory -Path $BinDir -Force | Out-Null }
if (-not (Test-Path $DataDir)) { New-Item -ItemType Directory -Path $DataDir -Force | Out-Null }

# ====== 3. Download uv if missing ======
Write-Step "Checking/downloading uv..."
if (Test-Path $UvExe) {
    Write-Ok "uv already exists: $UvExe"
} else {
    $uvUrl = "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"
    $uvZip = Join-Path $env:TEMP "uv.zip"
    Write-Host "    Downloading: $uvUrl"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    try {
        Invoke-WebRequest -Uri $uvUrl -OutFile $uvZip -UseBasicParsing
        $tmpExtract = Join-Path $env:TEMP "uv-extract"
        if (Test-Path $tmpExtract) { Remove-Item $tmpExtract -Recurse -Force }
        Expand-Archive -Path $uvZip -DestinationPath $tmpExtract -Force
        $found = Get-ChildItem $tmpExtract -Filter "uv.exe" -Recurse | Select-Object -First 1
        if (-not $found) { Die "uv.exe not found in archive" }
        Move-Item $found.FullName $UvExe -Force
        Remove-Item $uvZip -Force
        Remove-Item $tmpExtract -Recurse -Force
        Write-Ok "uv downloaded"
    } catch {
        Die "uv download failed: $_`nPlease download $uvUrl manually, extract uv.exe to $BinDir"
    }
}

# ====== 4. Download nssm if missing ======
Write-Step "Checking/downloading nssm..."
if (Test-Path $NssmExe) {
    Write-Ok "nssm already exists: $NssmExe"
} else {
    $nssmUrl = "https://nssm.cc/release/nssm-2.24.zip"
    $nssmZip = Join-Path $env:TEMP "nssm.zip"
    Write-Host "    Downloading: $nssmUrl"
    try {
        Invoke-WebRequest -Uri $nssmUrl -OutFile $nssmZip -UseBasicParsing
        $tmpExtract = Join-Path $env:TEMP "nssm-extract"
        if (Test-Path $tmpExtract) { Remove-Item $tmpExtract -Recurse -Force }
        Expand-Archive -Path $nssmZip -DestinationPath $tmpExtract -Force
        $found = Get-ChildItem $tmpExtract -Filter "nssm.exe" -Recurse |
                 Where-Object { $_.FullName -match "win64" } | Select-Object -First 1
        if (-not $found) { $found = Get-ChildItem $tmpExtract -Filter "nssm.exe" -Recurse | Select-Object -First 1 }
        if (-not $found) { Die "nssm.exe not found in archive" }
        Copy-Item $found.FullName $NssmExe -Force
        Remove-Item $nssmZip -Force
        Remove-Item $tmpExtract -Recurse -Force
        Write-Ok "nssm downloaded"
    } catch {
        Die "nssm download failed: $_`nPlease download $nssmUrl manually, extract win64\nssm.exe to $BinDir"
    }
}

# ====== 5. Sync Python dependencies ======
Write-Step "Syncing Python dependencies (uv will auto-download Python 3.13)..."
& $UvExe sync --frozen --no-dev
if ($LASTEXITCODE -ne 0) { Die "uv sync failed" }
Write-Ok "Dependencies synced"

# ====== 6. Create default config.json if missing ======
if (-not (Test-Path $ConfigFile)) {
    Write-Step "Creating default config.json..."
    $authKey = -join ((48..57) + (97..122) | Get-Random -Count 16 | ForEach-Object { [char]$_ })
    @{ "auth-key" = $authKey } | ConvertTo-Json | Set-Content -Path $ConfigFile -Encoding UTF8
    Write-Ok "config.json created"
    Write-Warn "auth-key = $authKey  (needed for Web UI login, write it down!)"
} else {
    Write-Ok "config.json already exists"
}

# ====== 7. Register Windows service ======
Write-Step "Registering Windows service: $ServiceName ..."
# Clean up any same-name old service (ignore errors if not exists)
Invoke-Nssm stop $ServiceName
Invoke-Nssm remove $ServiceName confirm

Invoke-Nssm install $ServiceName $RunBat
Invoke-Nssm set $ServiceName AppDirectory $ProjectRoot
Invoke-Nssm set $ServiceName AppStdout (Join-Path $DataDir "service.out.log")
Invoke-Nssm set $ServiceName AppStderr (Join-Path $DataDir "service.err.log")
Invoke-Nssm set $ServiceName AppRotateFiles 1
Invoke-Nssm set $ServiceName AppRotateBytes 10485760
Invoke-Nssm set $ServiceName Start SERVICE_AUTO_START
Invoke-Nssm set $ServiceName AppEnvironmentExtra "CHATGPT2API_PORT=$Port" "UV_PATH=$UvExe" "GIT_PYTHON_REFRESH=quiet"
Write-Ok "Service registered"

# ====== 8. Start service ======
Write-Step "Starting service..."
Invoke-Nssm start $ServiceName
Start-Sleep -Seconds 3

# ====== 9. Print result ======
try {
    $ip = (Get-NetIPAddress -AddressFamily IPv4 |
           Where-Object { $_.InterfaceAlias -notmatch "Loopback" -and $_.IPAddress -notmatch "^169\.254" } |
           Select-Object -First 1).IPAddress
} catch { $ip = "SERVER_IP" }

Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Ok "Deploy complete!"
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Web UI:    http://$ip`:$Port/" -ForegroundColor White
Write-Host "  Local:     http://127.0.0.1:$Port/" -ForegroundColor White
Write-Host ""
Write-Host "  Service management:" -ForegroundColor Gray
Write-Host "    Start:   $NssmExe start $ServiceName"
Write-Host "    Stop:    $NssmExe stop $ServiceName"
Write-Host "    Restart: $NssmExe restart $ServiceName"
Write-Host "    GUI:     services.msc  (name: $ServiceName)"
Write-Host ""
Write-Host "  Logs:" -ForegroundColor Gray
Write-Host "    $DataDir\service.out.log"
Write-Host "    $DataDir\service.err.log"
Write-Host ""
Write-Host "  Uninstall:" -ForegroundColor Gray
Write-Host "    powershell -ExecutionPolicy Bypass -File scripts\windows\deploy.ps1 -Uninstall"
Write-Host ""
