---
name: computer-use-setup
description: Setup, health diagnostics, and troubleshooting guide for Codex-style background Computer Use in Antigravity (combining cua-driver + cua_window_glow_proxy.py for same-color glowing border & virtual cursor on Windows GUI and chrome-devtools --autoConnect for logged-in Chrome control without hijacking the mouse).
---
<!-- Computer Use 環境設定與診斷技能：指導 Agent 如何檢查與修復 cua-driver、視窗同色螢光外框代理及 Chrome --autoConnect -->

# Computer Use Setup & Diagnostics

Use this skill when the user asks to check, set up, repair, customize, or test background Computer Use capabilities on Windows (both native desktop applications and the user's logged-in Chrome browser).

## 1. Architecture Overview

This plugin combines two non-intrusive control planes plus a Win32/DWM rounded glow border proxy so the agent never hijacks the user's physical mouse cursor:

1. **Background Windows GUI Control + Same-Color Window Glow Proxy (`cua-driver` + `bin/cua_window_glow_proxy.py`)**:
   - Uses Windows UI Automation (`IUIAutomationInvokePattern` / `ValuePattern`) and background window messages (`PostMessage`) with `delivery_mode: "background"`.
   - `cua_window_glow_proxy.py` wraps `cua-driver.exe mcp --grant existing-profile` in a Win32 Job Object (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`) to prevent orphaned processes.
   - Automatically extracts the target `window_id` (and maps `snapshot_id` / `element_token` back to `window_id`) and renders an 8px-rounded anti-aliased neon border matching the exact RGB color of `cua-driver`'s virtual cursor (`#4CCCE0` for `session: "agy-cursor"`, `#5EC0E8` for `session: "default"`).
   - Pins both the glowing window border and `Cua.AgentCursorOverlay` immediately above the target `window_id` in the Win32 Z-order (`SetWindowPos`) so they never bleed through onto foreground windows covering the target.
2. **Logged-in Chrome Control (`chrome-devtools` MCP with `--autoConnect` & `cua-driver` Browser Tools)**:
   - Connects directly to the user's active Chrome profile via Chrome DevTools Protocol (CDP), retaining all cookies, logins, and extensions.

## 2. Diagnostic Checklist

### A. Verify `cua-driver` Installation & Health

1. Call `cua-driver` MCP `health_report`.
2. Or run in PowerShell:
   ```powershell
   cua-driver doctor
   ```
3. If the binary is missing, install it with:
   ```powershell
   $env:CUA_DRIVER_NO_CONNECT='1'; irm https://raw.githubusercontent.com/trycua/cua/main/libs/cua-driver/scripts/install.ps1 | iex
   ```
4. If the user needs to control elevated (Administrator / High-IL) windows, instruct them to run once in an elevated PowerShell:
   ```powershell
   cua-driver autostart enable
   cua-driver autostart kick
   ```

### B. Preview or Customize the Window Glow Border

To test the rounded window glow border standalone on any `window_id` (`HWND`) from an interactive PowerShell:

```powershell
py -3 "$env:USERPROFILE\.gemini\config\plugins\computer-use\bin\cua_window_glow_proxy.py" --test-hwnd <window_id> agy-cursor
```

To customize the border color or hold duration, edit `env` in `~/.gemini/config/plugins/computer-use/mcp_config.json`:
- `"CUA_GLOW_COLOR"`: `"auto"` (default, automatically matches `cua-driver`'s virtual cursor color for the active session) or any 6-digit hex color such as `"#A855F7"`.
- `"CUA_GLOW_HOLD_SEC"`: `"3.5"` (seconds to keep the glow border visible after the last action before fading out).

### C. Verify Logged-in Chrome (`--autoConnect`)

1. Call the `chrome-devtools` MCP `list_pages` tool.
2. If it returns `Could not find DevToolsActivePort`:
   - Instruct the user to open `chrome://inspect/#remote-debugging` in their everyday Chrome browser (Chrome 144+) and check **Enable remote debugging**.
   - Re-test with `list_pages`.

### D. Run Full Environment Doctor & Auto-Update from GitHub

1. **One-command environment diagnostics** (checks Git, `py -3`, `npx`, `cua-driver`, proxy syntax, Git tracking, and Chrome `DevToolsActivePort`):
   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\.gemini\config\plugins\computer-use\scripts\doctor.ps1"
   ```
2. **Update plugin from GitHub** (`https://github.com/novoicefire/antigravity-computer-use`):
   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\.gemini\config\plugins\computer-use\scripts\update.ps1"
   ```
   - Add `-UpgradeDriver` to also upgrade `cua-driver.exe` to the latest upstream release.
   - If `mcp_config.json` or `bin/cua_window_glow_proxy.py` changed, instruct the user to toggle `[computer-use](customization://computer-use)` off and back on (or restart Antigravity) to reload the MCP server.
