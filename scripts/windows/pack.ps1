#Requires -Version 5.1
<#
.SYNOPSIS
    Pack chatgpt2api into a zip for Windows server deployment.
.DESCRIPTION
    Builds the Next.js frontend, then zips the project excluding node_modules/.venv/data.
    Output: dist\chatgpt2api.zip
.NOTES
    Usage (local dev machine):
      powershell -ExecutionPolicy Bypass -File scripts\windows\pack.ps1
#>
$ErrorActionPreference = 'Stop'

$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) { $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $ScriptDir)
Set-Location $ProjectRoot

function Write-Step($m){ Write-Host "`n[*] $m" -ForegroundColor Cyan }
function Write-Ok($m) { Write-Host "[OK] $m" -ForegroundColor Green }
function Die($m)      { Write-Host "[X] $m" -ForegroundColor Red; exit 1 }

# ====== 1. Build frontend ======
Write-Step "Building frontend (web_dist)..."
$webDir = Join-Path $ProjectRoot "web"
if (-not (Test-Path (Join-Path $webDir "node_modules"))) {
    Write-Host "    node_modules missing, running npm install..."
    Push-Location $webDir
    npm install --registry=https://registry.npmmirror.com --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { Die "npm install failed" }
    Pop-Location
}

Push-Location $webDir
$env:NEXT_PUBLIC_APP_VERSION = (Get-Content (Join-Path $ProjectRoot "VERSION")).Trim()
npm run build
if ($LASTEXITCODE -ne 0) { Die "npm run build failed" }
Pop-Location

# Copy web/out -> web_dist
$webDist = Join-Path $ProjectRoot "web_dist"
if (Test-Path $webDist) { Remove-Item $webDist -Recurse -Force }
Copy-Item -Recurse (Join-Path $webDir "out") $webDist
Write-Ok "web_dist built"

# ====== 2. Prepare dist dir ======
$distDir = Join-Path $ProjectRoot "dist"
if (-not (Test-Path $distDir)) { New-Item -ItemType Directory -Path $distDir -Force | Out-Null }
$zipPath = Join-Path $distDir "chatgpt2api.zip"
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }

# ====== 3. Zip project (exclude heavy/runtime dirs) ======
Write-Step "Packing zip (excluding node_modules, .venv, data, etc.)..."

# Build exclude list for Compress-Archive (it doesn't support excludes natively,
# so we stage a temp folder)
$stageDir = Join-Path $env:TEMP "chatgpt2api-pack-$(Get-Random)"
if (Test-Path $stageDir) { Remove-Item $stageDir -Recurse -Force }
New-Item -ItemType Directory -Path $stageDir -Force | Out-Null

$excludeNames = @(
    "node_modules", ".venv", ".git", "data", "dist",
    ".next", "out", "__pycache__", ".pytest_cache",
    ".idea", ".cursor", ".trae", ".codegraph",
    "scripts\windows\bin"
) | ForEach-Object { $_.TrimEnd('\') }

$includeItems = @(
    "api", "services", "utils", "scripts", "web_dist",
    "main.py", "pyproject.toml", "uv.lock", "VERSION",
    "CHANGELOG.md", "README.md", "Dockerfile",
    "docker-compose.yml", "docker-compose.local.yml",
    ".env.example", ".dockerignore"
)

foreach ($item in $includeItems) {
    $src = Join-Path $ProjectRoot $item
    if (Test-Path $src) {
        Copy-Item $src (Join-Path $stageDir $item) -Recurse -Force
    }
}

# Always keep deploy/update scripts (run.bat too)
$winScriptsSrc = Join-Path $ProjectRoot "scripts\windows"
$winScriptsDst = Join-Path $stageDir "scripts\windows"
if (-not (Test-Path $winScriptsDst)) { New-Item -ItemType Directory -Path $winScriptsDst -Force | Out-Null }
foreach ($f in @("deploy.ps1", "update.ps1", "run.bat")) {
    $src = Join-Path $winScriptsSrc $f
    if (Test-Path $src) { Copy-Item $src (Join-Path $winScriptsDst $f) -Force }
}

Compress-Archive -Path (Join-Path $stageDir "*") -DestinationPath $zipPath -Force
Remove-Item $stageDir -Recurse -Force

$sizeMB = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
Write-Ok "Zip created: $zipPath ($sizeMB MB)"
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Upload $zipPath to server (e.g. C:\chatgpt2api.zip)"
Write-Host "  2. On server (Admin PowerShell):"
Write-Host "       powershell -ExecutionPolicy Bypass -File C:\chatgpt2api\scripts\windows\update.ps1 -ZipPath C:\chatgpt2api.zip"
Write-Host ""
