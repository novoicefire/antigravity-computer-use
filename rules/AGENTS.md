<!-- Computer Use 插件全域規則：強制執行「背景優先、不搶實體滑鼠、不搶視窗焦點、游標與同色視窗螢光框綁定目標圖層與分頁」的操作規範 -->
# Computer Use (Background Desktop & Logged-in Chrome) Rules

When operating or inspecting the user's browser or desktop applications, always preserve the user's physical mouse cursor and foreground window focus, and keep the virtual AI cursor and same-color glowing window border pinned to the target window's Z-order layer (and active tab for Chrome):

1. **Per-Window Z-Order Cursor & Same-Color Glow Border (`cua-driver` + `cua_window_glow_proxy.py`)**:
   - On Windows, `cua-driver` and `cua_window_glow_proxy.py` automatically pin BOTH `Cua.AgentCursorOverlay` and the rounded neon window border (`Cua.TargetWindowGlowBorder`) **immediately above the target `window_id` in the Z-order** whenever you call a window-bound action tool (`get_window_state`, `click`, `double_click`, `right_click`, `type_text`, `set_value`, `press_key`, `scroll`, `drag`, `run_steps`, `get_browser_state`, `browser_click`, `browser_type`, `browser_pointer`).
   - The window glow border color automatically syncs with the session's virtual cursor color (`session: "agy-cursor"` -> `#4CCCE0`, `session: "default"` -> `#5EC0E8`).
   - **CRITICAL: Do NOT call `move_cursor` when operating or inspecting background windows!** Calling `move_cursor` detaches the cursor overlay from the target window's Z-order and promotes it to `HWND_TOPMOST` (causing the cursor to bleed through on top of the user's foreground `Antigravity` window).
   - Keep `idle_hide_ms: 1500` via `set_agent_cursor_motion` so the cursor fades out cleanly after completing an action.
   - Always pass `session: "agy-cursor"` to `cua-driver` calls.

2. **Logged-in Chrome Browser — Tab-Aware Background Operation (`cua-driver` Browser Tools + `@chrome-tab` + Side Panel)**:
   - For operating a specific Chrome tab in the background (without disturbing the user's other open tabs in the same Chrome window), ALWAYS prefer `cua-driver`'s typed browser tools (`list_windows` -> `get_browser_state` -> `browser_prepare` with `strategy: {"kind": "existing_profile"}` -> `get_browser_state(target_id, tab_id, snapshot_format="semantic_v2")` -> `browser_click` / `browser_type` / `browser_pointer` / `browser_navigate` with `session: "agy-cursor"`).
   - **Tab-Aware Visual Feedback**: `cua-driver` + `cua_window_glow_proxy.py` operate the specified `tab_id` completely in the background without switching the user's selected tab. When the user switches TO that tab, both the same-color rounded glow border and the virtual cursor appear immediately; when the user switches AWAY to another tab, both immediately hide.
   - If the user attaches a Chrome tab via **`@chrome-tab-...`** or the **`Chrome Tabs`** side panel (`open_chrome_tabs_picker`), **directly pass the provided `target_id` and `tab_id` (plus `session: "agy-cursor"`) to `get_browser_state` / `browser_click` / `browser_pointer` / `browser_type` / `browser_navigate`** — do NOT manually call bind-mode `get_browser_state(pid, window_id)` first, as `cua_window_glow_proxy.py` automatically heals stale/restarted IDs and transparently falls back to CDP coordinate/screenshot rescue (including `<canvas>` pages such as Chrome Remote Desktop) if multi-window `heuristic` occurs.
   - Use `chrome-devtools` MCP (`--autoConnect`, also wrapped with Tab-Aware glow & cursor feedback via `--devtools-proxy`) when you specifically need DevTools Console, Network request inspection, CSS styles, rich-text/CodeMirror `evaluate_script`, or Lighthouse/heap debugging.

3. **Native Windows Desktop GUI & Qt/LINE Golden Rules (`cua-driver` MCP)**:
   - Default to `delivery_mode: "background"` for all desktop GUI interactions (`click`, `type_text`, `set_value`, `press_key`, `hotkey`, `scroll`).
   - Always snapshot the target window first via `get_window_state({ pid, window_id, session: "agy-cursor" })` and act via `element_token` whenever possible.
   - Never use focus-stealing shell commands (`Start-Process`, `cmd /c start`, `SendInput`) to launch or control GUI apps; always use `cua-driver`'s `launch_app`.
   - **Qt / LINE Input & `Enter` Key Rule**:
     - For live-filtering search boxes (such as LINE's `"搜尋聊天和訊息"`), `set_value` triggers live filtering automatically after ~0.5s — do **NOT** send `press_key("return")`, and instead re-read with `get_window_state({ query: "<keyword>" })`.
     - When pressing `return` to submit a chat message (such as LINE's `"輸入訊息"` field), **ALWAYS pass the input field's `element_token`** (e.g. `press_key({ element_token: "<snapshot_id>:N", key: "return", session: "agy-cursor" })`). Never send an untargeted `press_key("return")` to an unfocused Qt/LINE `window_id` without `element_token`, as Qt routes unfocused `VK_RETURN` to the window's first titlebar button (`"視窗最小化"`).
   - Only escalate to `delivery_mode: "foreground"` if an action returns `background_unavailable` AND after informing the user.

4. **Safety & External Action Guardrails**:
   - Before sending any external communication (LINE message, email, forum post), verify that the target chat window title / recipient name matches the user's requested recipient (e.g. distinguish exact recipient names from similar-looking names).
   - If the user only asks to draft, search, or inspect, do NOT press send.
   - Never execute financial transactions, payments, destructive file deletions, or account/password changes without explicit user confirmation.
