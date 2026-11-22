# Computer Use 外掛一鍵自動安裝腳本：自動檢查並補齊相依環境、Clone/更新外掛至 Antigravity 並執行健檢
param(
    [string]$RepoUrl = "https://github.com/novoicefire/antigravity-computer-use.git",
    [string]$Branch = "main",
    [string]$PluginDir = (Join-Path $env:USERPROFILE ".gemini\config\plugins\computer-use"),
    [switch]$SkipWinget,
    [switch]$SkipDoctor
)

$ErrorActionPreference = "Stop"

if ($env:CUA_SKIP_WINGET -eq "1") { $SkipWinget = $true }
if ($env:CUA_SKIP_DOCTOR -eq "1") { $SkipDoctor = $true }

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Refresh-SessionPath {
    $machinePath = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [System.Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machinePath;$userPath"
}

function Install-WingetPackage {
    param([string]$Id, [string]$DisplayName)
    if ($SkipWinget) {
        Write-Warning "$DisplayName is missing and SkipWinget is enabled. Please install '$Id' manually."
        return $false
    }
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        Write-Warning "winget is not available. Please install $DisplayName manually."
        return $false
    }
    Write-Host "Installing $DisplayName via winget ($Id)..." -ForegroundColor Yellow
    & winget install --id $Id -e --source winget --accept-package-agreements --accept-source-agreements
    Refresh-SessionPath
    return $true
}

function Find-CuaDriver {
    $candidates = @(
        $env:CUA_DRIVER_BIN,
        (Join-Path $env:LOCALAPPDATA "Programs\Cua\cua-driver\bin\cua-driver.exe"),
        (Join-Path $env:USERPROFILE "scoop\apps\cua-driver\current\cua-driver.exe")
    )
    foreach ($c in $candidates) {
        if ($c -and (Test-Path $c)) { return $c }
    }
    $cmd = Get-Command cua-driver -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

Write-Host "=====================================================" -ForegroundColor Cyan
Write-Host " Antigravity Computer Use Plugin - Automated Setup" -ForegroundColor Cyan
Write-Host "=====================================================" -ForegroundColor Cyan

if ($env:OS -ne "Windows_NT") {
    throw "This installer currently supports Windows 10/11 only."
}

# Step 1: Ensure Git
Write-Step "1/5 Checking Git..."
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Install-WingetPackage -Id "Git.Git" -DisplayName "Git" | Out-Null
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git is required to install and update this plugin. Please install Git and re-run."
}
Write-Host ("Git ready: " + ((& git --version) -join " ")) -ForegroundColor Green

# Step 2: Ensure Python 3 (with py launcher)
Write-Step "2/5 Checking Python 3 (py -3)..."
$hasPy3 = $false
if (Get-Command py -ErrorAction SilentlyContinue) {
    $null = & py -3 --version 2>&1
    if ($LASTEXITCODE -eq 0) { $hasPy3 = $true }
}
if (-not $hasPy3) {
    Install-WingetPackage -Id "Python.Python.3.12" -DisplayName "Python 3.12" | Out-Null
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $null = & py -3 --version 2>&1
        if ($LASTEXITCODE -eq 0) { $hasPy3 = $true }
    }
}
if ($hasPy3) {
    Write-Host ("Python ready: " + ((& py -3 --version) -join " ")) -ForegroundColor Green
} else {
    Write-Warning "'py -3' is still unavailable in this shell. If Python was just installed, open a new terminal or verify python.org launcher."
}

# Step 3: Ensure Node.js / npx (for chrome-devtools-mcp)
Write-Step "3/5 Checking Node.js / npx..."
if (-not (Get-Command npx -ErrorAction SilentlyContinue)) {
    Install-WingetPackage -Id "OpenJS.NodeJS.LTS" -DisplayName "Node.js LTS" | Out-Null
}
if (Get-Command npx -ErrorAction SilentlyContinue) {
    Write-Host ("Node.js ready: " + ((& node --version) -join " ")) -ForegroundColor Green
} else {
    Write-Warning "Node.js / npx not detected. chrome-devtools MCP requires Node.js LTS."
}

# Step 4: Ensure cua-driver
Write-Step "4/5 Checking cua-driver..."
$cuaBin = Find-CuaDriver
if (-not $cuaBin) {
    Write-Host "Installing cua-driver via official installer (CUA_DRIVER_NO_CONNECT=1)..." -ForegroundColor Yellow
    $prevNoConnect = $env:CUA_DRIVER_NO_CONNECT
    $env:CUA_DRIVER_NO_CONNECT = "1"
    try {
        Invoke-RestMethod "https://raw.githubusercontent.com/trycua/cua/main/libs/cua-driver/scripts/install.ps1" | Invoke-Expression
    } finally {
        if ($null -eq $prevNoConnect) {
            Remove-Item Env:\CUA_DRIVER_NO_CONNECT -ErrorAction SilentlyContinue
        } else {
            $env:CUA_DRIVER_NO_CONNECT = $prevNoConnect
        }
    }
    Refresh-SessionPath
    $cuaBin = Find-CuaDriver
}
if ($cuaBin) {
    Write-Host ("cua-driver ready: " + ((& $cuaBin --version 2>&1) -join " ") + " ($cuaBin)") -ForegroundColor Green
} else {
    Write-Warning "cua-driver installation could not be verified automatically. Check your network and retry."
}

# Step 5: Clone or update the plugin directly into Antigravity's plugin folder
Write-Step "5/5 Installing plugin into $PluginDir..."
$pluginsRoot = Split-Path -Parent $PluginDir
if (-not (Test-Path $pluginsRoot)) {
    New-Item -ItemType Directory -Path $pluginsRoot -Force | Out-Null
}

if (Test-Path (Join-Path $PluginDir ".git")) {
    Write-Host "Existing git-tracked plugin detected. Updating with git pull --ff-only..." -ForegroundColor Yellow
    $dirty = (& git -C $PluginDir status --porcelain)
    if ($dirty) {
        Write-Warning "Local uncommitted changes detected in $PluginDir. Skipping git pull to protect your edits."
    } else {
        & git -C $PluginDir fetch --tags origin
        & git -C $PluginDir pull --ff-only origin $Branch
    }
} elseif (Test-Path $PluginDir) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $backupDir = "$PluginDir.backup-$stamp"
    Write-Host "Existing non-git folder detected. Backing up to $backupDir..." -ForegroundColor Yellow
    Move-Item -Path $PluginDir -Destination $backupDir
    & git clone --branch $Branch $RepoUrl $PluginDir
} else {
    & git clone --branch $Branch $RepoUrl $PluginDir
}

# Run doctor
if (-not $SkipDoctor) {
    $doctorScript = Join-Path $PluginDir "scripts\doctor.ps1"
    if (Test-Path $doctorScript) {
        & $doctorScript -PluginDir $PluginDir
    }
}

Write-Host ""
Write-Host "=====================================================" -ForegroundColor Green
Write-Host " Setup Complete!" -ForegroundColor Green
Write-Host "=====================================================" -ForegroundColor Green
Write-Host "Next steps:"
Write-Host "  1. Open Chrome (144+) -> chrome://inspect/#remote-debugging"
Write-Host "     Check 'Enable remote debugging' (one-time setup for logged-in Chrome)."
Write-Host "  2. In Antigravity, reload/restart if the plugin is newly installed, then ensure 'computer-use' is enabled."
Write-Host "  3. Try asking Antigravity: 'List all my open Chrome tabs in the background.'"
Write-Host ""
