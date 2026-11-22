<!-- Agent 自動安裝與驗證手冊：供其他電腦的 Google Antigravity 讀取並全自動完成 computer-use 外掛部署與環境檢查 -->
# Automated Installation Guide for Antigravity Agents

If a user asked you to install, set up, or update the **`computer-use`** plugin from `https://github.com/novoicefire/antigravity-computer-use`, follow these exact steps on Windows 10/11.

## Prerequisites & Safety Rules
- Target OS: **Windows 10 / 11** (uses Win32 UI Automation, DWM rounded overlay, and Job Objects).
- Do **NOT** create an NTFS junction or symlink for `~/.gemini/config/plugins/computer-use` — Antigravity's plugin loader skips junctions. Clone directly into the real directory `%USERPROFILE%\.gemini\config\plugins\computer-use`.
- Do **NOT** hand-edit `~/.gemini/config/config.json` to enable plugins; direct the user to `[computer-use](customization://computer-use)` if toggling is needed.
- Always run `cua-driver` installer with `CUA_DRIVER_NO_CONNECT=1` so it does not prompt interactively.

## Step 1: Run the One-Command Installer
Run the following PowerShell command via `run_command`:

```powershell
irm https://raw.githubusercontent.com/novoicefire/antigravity-computer-use/main/scripts/install.ps1 | iex
```

What this script automatically performs:
1. Checks and installs **Git**, **Python 3.12 (`py -3`)**, and **Node.js LTS (`npx`)** via `winget` if missing.
2. Checks and installs **`cua-driver`** (`%LOCALAPPDATA%\Programs\Cua\cua-driver\bin\cua-driver.exe`) with `CUA_DRIVER_NO_CONNECT=1`.
3. Clones `https://github.com/novoicefire/antigravity-computer-use.git` directly into `%USERPROFILE%\.gemini\config\plugins\computer-use` (or runs `git pull --ff-only` if already git-managed).
4. Executes `scripts/doctor.ps1` to verify Python syntax, required plugin files, `cua-driver doctor`, and Chrome remote debugging status.

> **Fallback if `py` launcher is missing:** If the machine only has `python` (e.g. Store Python or Conda) and `py -3` still fails after installation, check `python --version` (>= 3.9). Instruct the user to install the official Python Launcher from python.org (`winget install --id Python.Python.3.12 -e`) or temporarily set `"command": "python"` and remove `"-3"` in `%USERPROFILE%\.gemini\config\plugins\computer-use\mcp_config.json`.

## Step 2: Check Chrome Remote Debugging (One-Time User Action)
Check whether `%LOCALAPPDATA%\Google\Chrome\User Data\DevToolsActivePort` exists (already reported by `doctor.ps1`).
- If **`[WARN] Chrome Remote Debugging`**:
  Instruct the user to complete this 5-second one-time setting in their regular Chrome browser (Chrome 144+):
  1. Open a new tab and navigate to `chrome://inspect/#remote-debugging`
  2. Check **Enable remote debugging** (允許遠端偵錯)
  3. Keep Chrome open and tell you when done.

## Step 3: Activate & Verify Inside Antigravity
1. Tell the user the plugin is installed at `%USERPROFILE%\.gemini\config\plugins\computer-use` and link them to `[computer-use](customization://computer-use)` (if newly installed, they may need to reload/restart Antigravity once so the new MCP servers `cua-driver` and `chrome-devtools` start).
2. Once the MCP tools are live, run a quick non-intrusive verification:
   - Call `computer-use_cua-driver` → `health_report` with `session: "agy-cursor"`.
   - Call `computer-use_cua-driver` → `list_windows` with `session: "agy-cursor"` to confirm background window inspection works without stealing focus.
   - If Chrome remote debugging is enabled, call `computer-use_chrome-devtools` → `list_pages` (or `cua-driver` `get_browser_state`) to confirm logged-in tab access works.

## Step 4: Future Updates
Whenever the user asks to update `computer-use`, run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\.gemini\config\plugins\computer-use\scripts\update.ps1"
```

Pass `-UpgradeDriver` to also update the underlying `cua-driver` binary to the latest upstream release.
