# Computer Use 外掛環境診斷腳本：檢查 Git、Python、Node.js、cua-driver、外掛檔案與 Chrome 遠端偵錯狀態
param(
    [string]$PluginDir = (Join-Path $env:USERPROFILE ".gemini\config\plugins\computer-use")
)

$ErrorActionPreference = "Continue"

function Write-Check {
    param([string]$Level, [string]$Label, [string]$Detail)
    switch ($Level) {
        "PASS" { Write-Host "[PASS] " -ForegroundColor Green -NoNewline }
        "WARN" { Write-Host "[WARN] " -ForegroundColor Yellow -NoNewline }
        "FAIL" { Write-Host "[FAIL] " -ForegroundColor Red -NoNewline }
        default { Write-Host "[INFO] " -ForegroundColor Cyan -NoNewline }
    }
    Write-Host ("{0,-28} {1}" -f $Label, $Detail)
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

Write-Host ""
Write-Host "=== Antigravity Computer Use Doctor ===" -ForegroundColor Cyan
Write-Host "Plugin Directory: $PluginDir"
Write-Host ""

$failCount = 0
$warnCount = 0

# 1. OS check
if ($env:OS -eq "Windows_NT") {
    $osVer = [System.Environment]::OSVersion.Version
    Write-Check "PASS" "Operating System" "Windows ($osVer)"
} else {
    Write-Check "FAIL" "Operating System" "This plugin currently targets Windows 10/11."
    $failCount++
}

# 2. Git check
$gitCmd = Get-Command git -ErrorAction SilentlyContinue
if ($gitCmd) {
    $gitVer = (& git --version 2>&1) -join " "
    Write-Check "PASS" "Git" $gitVer
} else {
    Write-Check "FAIL" "Git" "Not found in PATH. Install via: winget install --id Git.Git -e"
    $failCount++
}

# 3. Python launcher (py -3) check
$pyCmd = Get-Command py -ErrorAction SilentlyContinue
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if ($pyCmd) {
    $pyVerRaw = (& py -3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2>&1) -join ""
    if ($LASTEXITCODE -eq 0 -and $pyVerRaw -match '^(\d+)\.(\d+)') {
        $major = [int]$Matches[1]
        $minor = [int]$Matches[2]
        if ($major -ge 3 -and $minor -ge 9) {
            Write-Check "PASS" "Python Launcher (py -3)" "Python $pyVerRaw"
        } else {
            Write-Check "FAIL" "Python Launcher (py -3)" "Python $pyVerRaw is too old (need >= 3.9, recommended 3.10+)."
            $failCount++
        }
    } else {
        Write-Check "FAIL" "Python Launcher (py -3)" "py launcher found, but 'py -3' failed: $pyVerRaw"
        $failCount++
    }
} elseif ($pythonCmd) {
    $pyVerRaw = (& python --version 2>&1) -join ""
    Write-Check "WARN" "Python Launcher (py -3)" "'py' launcher missing (found $pyVerRaw). Install python.org Python or update mcp_config.json command to 'python'."
    $warnCount++
} else {
    Write-Check "FAIL" "Python Launcher (py -3)" "Python 3 not found. Install via: winget install --id Python.Python.3.12 -e"
    $failCount++
}

# 4. Node.js / npx check
$npxCmd = Get-Command npx -ErrorAction SilentlyContinue
if ($npxCmd) {
    $nodeVer = (& node --version 2>&1) -join ""
    Write-Check "PASS" "Node.js / npx" "node $nodeVer (npx ready for chrome-devtools-mcp)"
} else {
    Write-Check "WARN" "Node.js / npx" "Not found (needed for chrome-devtools MCP). Install via: winget install --id OpenJS.NodeJS.LTS -e"
    $warnCount++
}

# 5. cua-driver binary check
$cuaBin = Find-CuaDriver
if ($cuaBin) {
    $cuaVer = (& $cuaBin --version 2>&1) -join " "
    Write-Check "PASS" "cua-driver Binary" "$cuaVer ($cuaBin)"
} else {
    Write-Check "FAIL" "cua-driver Binary" "Not installed. Run scripts/install.ps1 to install automatically."
    $failCount++
}

# 6. Plugin files integrity check
$requiredFiles = @(
    "plugin.json",
    "mcp_config.json",
    "bin\cua_window_glow_proxy.py",
    "bin\cua_cdp_rescue.py",
    "bin\cua_devtools_glow_proxy.py",
    "ui\chrome_tabs_picker.html",
    "rules\AGENTS.md",
    "skills\computer-use-setup\SKILL.md"
)
$missingFiles = @()
foreach ($rel in $requiredFiles) {
    if (-not (Test-Path (Join-Path $PluginDir $rel))) {
        $missingFiles += $rel
    }
}
if ($missingFiles.Count -eq 0) {
    Write-Check "PASS" "Plugin Files" "All core plugin files present."
    if ($pyCmd) {
        $pyFiles = @(
            "bin\cua_window_glow_proxy.py",
            "bin\cua_cdp_rescue.py",
            "bin\cua_devtools_glow_proxy.py"
        )
        $syntaxErrs = @()
        foreach ($pf in $pyFiles) {
            $fullPf = Join-Path $PluginDir $pf
            $cOut = (& py -3 -B -c "import ast, sys; ast.parse(open(sys.argv[1], 'r', encoding='utf-8').read())" $fullPf 2>&1) -join " "
            if ($LASTEXITCODE -ne 0) {
                $syntaxErrs += "$pf : $cOut"
            }
        }
        if ($syntaxErrs.Count -eq 0) {
            $binDir = Join-Path $PluginDir "bin"
            $impOut = (& py -3 -B -c "import sys; sys.path.insert(0, sys.argv[1]); import cua_cdp_rescue, cua_window_glow_proxy, cua_devtools_glow_proxy" $binDir 2>&1) -join " "
            if ($LASTEXITCODE -eq 0) {
                Write-Check "PASS" "Proxy Syntax & Import" "All Python proxy & rescue modules loaded cleanly."
            } else {
                Write-Check "FAIL" "Proxy Syntax & Import" $impOut
                $failCount++
            }
        } else {
            Write-Check "FAIL" "Proxy Syntax Check" ($syntaxErrs -join " | ")
            $failCount++
        }
    }
} else {
    Write-Check "FAIL" "Plugin Files" ("Missing: " + ($missingFiles -join ", "))
    $failCount++
}

# 7. Git repository status check
if (Test-Path (Join-Path $PluginDir ".git")) {
    $branch = (& git -C $PluginDir rev-parse --abbrev-ref HEAD 2>&1) -join ""
    $rev = (& git -C $PluginDir rev-parse --short HEAD 2>&1) -join ""
    Write-Check "PASS" "Git Tracking" "Branch '$branch' @ $rev (ready for git pull updates)"
} else {
    Write-Check "WARN" "Git Tracking" "Not a git repository; automatic updates via update.ps1 will require re-cloning."
    $warnCount++
}

# 8. Chrome Remote Debugging (DevToolsActivePort) check
$devtoolsPort = Join-Path $env:LOCALAPPDATA "Google\Chrome\User Data\DevToolsActivePort"
if (Test-Path $devtoolsPort) {
    Write-Check "PASS" "Chrome Remote Debugging" "DevToolsActivePort found (Chrome --autoConnect & existing-profile ready)."
} else {
    Write-Check "WARN" "Chrome Remote Debugging" "DevToolsActivePort not found. Open Chrome -> chrome://inspect/#remote-debugging -> Enable remote debugging."
    $warnCount++
}

# 9. Optional cua-driver doctor summary
if ($cuaBin) {
    Write-Host ""
    Write-Host "--- cua-driver doctor ---" -ForegroundColor DarkCyan
    & $cuaBin doctor
}

Write-Host ""
Write-Host "=== Summary: $failCount failure(s), $warnCount warning(s) ===" -ForegroundColor Cyan
if ($failCount -gt 0) {
    exit 1
}
exit 0
