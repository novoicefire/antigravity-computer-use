# Computer Use 外掛一鍵更新腳本：透過 git pull 拉取最新版本並可選配升級 cua-driver
param(
    [string]$PluginDir = (Join-Path $env:USERPROFILE ".gemini\config\plugins\computer-use"),
    [string]$Branch = "main",
    [switch]$UpgradeDriver,
    [switch]$SkipDoctor
)

$ErrorActionPreference = "Stop"

if ($env:CUA_UPGRADE_DRIVER -eq "1") { $UpgradeDriver = $true }

Write-Host "=== Antigravity Computer Use Updater ===" -ForegroundColor Cyan

if (-not (Test-Path (Join-Path $PluginDir ".git"))) {
    throw "Plugin directory '$PluginDir' is not a git repository. Run scripts/install.ps1 to convert it to a git-managed install."
}

$dirty = (& git -C $PluginDir status --porcelain)
if ($dirty) {
    Write-Host "Local uncommitted changes detected in ${PluginDir}:" -ForegroundColor Yellow
    $dirty | ForEach-Object { Write-Host "  $_" -ForegroundColor Yellow }
    throw "Aborting update to protect your local changes. Please commit or 'git stash' them first."
}

$beforeRev = (& git -C $PluginDir rev-parse --short HEAD)
Write-Host "Current revision: $beforeRev"
Write-Host "Fetching latest updates from origin/$Branch..." -ForegroundColor Cyan
& git -C $PluginDir fetch --tags origin

$behindCount = (& git -C $PluginDir rev-list --count "HEAD..origin/$Branch")
if ([int]$behindCount -gt 0) {
    Write-Host "Incoming commits ($behindCount):" -ForegroundColor Green
    & git -C $PluginDir log --oneline "HEAD..origin/$Branch"
    & git -C $PluginDir pull --ff-only origin $Branch
    $afterRev = (& git -C $PluginDir rev-parse --short HEAD)
    Write-Host "Updated successfully: $beforeRev -> $afterRev" -ForegroundColor Green
} else {
    Write-Host "Plugin is already up to date ($beforeRev)." -ForegroundColor Green
}

if ($UpgradeDriver) {
    Write-Host ""
    Write-Host "Upgrading cua-driver binary..." -ForegroundColor Cyan
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
}

if (-not $SkipDoctor) {
    $doctorScript = Join-Path $PluginDir "scripts\doctor.ps1"
    if (Test-Path $doctorScript) {
        & $doctorScript -PluginDir $PluginDir
    }
}

Write-Host ""
Write-Host "Tip: If mcp_config.json or cua_window_glow_proxy.py changed, toggle the plugin off/on in Antigravity Customizations (or restart Antigravity) to reload the MCP server." -ForegroundColor DarkGray
