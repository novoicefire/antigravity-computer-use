<!-- Computer Use 外掛版本異動紀錄：追蹤每次功能新增、修正與相容性改善 -->
# Changelog

All notable changes to **antigravity-computer-use** will be documented in this file.

## [1.4.0] - 2026-11-26

### Added
- **多視窗 `heuristic` 與 Chrome 遠端桌面 `<canvas>` 自動 CDP 救援通道 (`bin/cua_cdp_rescue.py`)**：
  - 當同時開啟多個最大化 Chrome 視窗、高 DPI 縮放或遠端桌面服務導致上游 `cua-driver.exe` 落入 `heuristic (title-only)` 唯讀限制時，代理層自動無縫切換至本機 `DevToolsActivePort` WebSocket 執行背景截圖 (`Page.captureScreenshot`)、`<canvas>` 像素座標滑鼠點擊／雙擊／右鍵／拖曳／滾輪 (`Input.dispatchMouseEvent`) 與鍵盤輸入 (`Input.dispatchKeyEvent`)，並同步驅動同色圓角螢光外框與 `Cua.ProxyCursorOverlay` 分頁感知虛擬游標，全程不搶本機實體滑鼠。
- **`chrome-devtools` MCP 視覺外框與虛擬游標代理 (`bin/cua_devtools_glow_proxy.py`、`mcp_config.json`)**：
  - 為 `chrome-devtools-mcp --autoConnect` 掛載 `--devtools-proxy`（含 Win32 Job Object 防孤兒行程），自動追蹤 `pageId` 與 `[selected]` 分頁標題，使 DevTools 操作同樣具備 Tab-Aware 同色圓角螢光外框 (`#4CCCE0`) 與虛擬游標回饋。

### Fixed
- **修正高解析度（2.8K / 4K 200% DPI）最大化視窗光暈過細與螢幕截圖不可見問題 (`bin/cua_window_glow_proxy.py`、`bin/cua_cdp_rescue.py`)**：
  - 根因：最大化視窗貼齊螢幕邊緣時，純外側光暈會被螢幕邊界與工作列裁切，在 `2880×1620`（200% DPI）螢幕上僅剩極細實心線；且先前預設啟用 `WDA_EXCLUDEFROMCAPTURE` 導致 `get_desktop_state` 截圖無法拍到外框。
  - 修正：改為 **DPI 自適應雙向圓角霓虹光暈**（200% DPI 下具備 `8px` 高亮白芯青色邊框 + 向內平滑漸層 `60px` 霓虹內光暈 + 向外 `28px` 外光暈），最大化視窗亦能完整呈現飽滿柔和光暈且點擊完全穿透；並將 `CUA_EXCLUDE_FROM_CAPTURE` 預設改為 `false` 以便截圖／錄影直接驗證。
  - 新增「前景揭曉計時（Foreground-Reveal Hold）」：當 Chrome 在背景被 Antigravity 蓋住時暫緩倒數，待使用者切回目標分頁才開始計算完整 9 秒呼吸光暈。
- **徹底阻絕跨分頁／開新分頁（`Ctrl+T`「新分頁」）虛擬游標與外框殘留 Bug (`bin/cua_window_glow_proxy.py`、`bin/cua_cdp_rescue.py`)**：
  - 新增 **60 FPS（`16ms`）全時段跨分頁游標防漏守衛（`_guard_browser_hwnd`）**，即使外框已淡出或 `cua-driver` 游標仍在停留時間內，只要使用者切分頁或開啟「新分頁」，`<16ms` 內立即強制隱藏 `Cua.AgentCursorOverlay` 與 `Cua.ProxyCursorOverlay`。
  - 嚴格禁止瀏覽器操作在缺少分頁標題時退化為全視窗顯示（`is_browser=True` 強制要求三層快取核對 `expected_tab_title`），並移除盲目讀取視窗標題的寬限期，改為僅接受該 `tab_id` 回傳的真實標題更新。
- **修正離螢幕輔助視窗干擾、跨 MCP 重啟綁定失效與 `browser_ref_stale` 自動重試 (`bin/cua_window_glow_proxy.py`)**：
  - 過濾不可見／離螢幕背景輔助視窗（如 `Chrome 遠端桌面 [off-screen]`），避免洗掉主視窗 `target_id`，同時完整保留可見的遠端桌面 PWA 視窗與 `remotedesktop.google.com` 分頁。
  - 新增 `tab_bindings_cache.json` 跨重啟持久化快取、漏傳 `session` 自動補齊 `"agy-cursor"`、背景重綁閒置門檻拉長至 `90s`，以及遇到 `browser_ref_stale` 時在 `0.2s` 內自動重抓快照依 `(role, name)` 無縫重試。

## [1.3.1] - 2026-11-25

### Fixed
- **修正刷新分頁後網站綁定失效的問題 (`bin/cua_window_glow_proxy.py`、`ui/chrome_tabs_picker.html`)**：
  - 根因：`cua-driver` 每次呼叫 `get_browser_state(pid, window_id)` 重新掃描分頁時都會重新配發新的 `target_id` 與 `tab_id` UUID，導致側邊欄誤判已綁定分頁已關閉而清除勾選與對話框 Context Pill，同時也讓先前貼入對話框的舊 `target_id`/`tab_id` 過期。
  - 修正 1（跨刷新持久 `stable_id`）：代理層為每個分頁配發跨刷新不變的 `stable_id`（支援分頁拖曳排序、未讀數字變動與同分頁導覽追蹤），前端改以 `stable_id` 維護勾選狀態並自動無縫同步最新底層 ID 至對話框 Context Pill，無論手動刷新或自動刷新多少次都不再掉綁定。
  - 修正 2（舊版 `target_id`/`tab_id` 透明自動映射）：代理層攔截 `get_browser_state` 與 `browser_*` 呼叫時，若偵測到傳入刷新前的舊 `tab_id`/`target_id`，自動透明替換為最新有效綁定 ID，徹底消除 `is not a live binding in this session` 錯誤，並在 AI 執行瀏覽器操作期間自動暫緩背景重綁 20 秒。

## [1.3.0] - 2026-11-25

### Added
- **「`● 瀏覽中`」即時分頁標記與 `@chrome-tab-current` 同步**：
  - 後端代理層 (`bin/cua_window_glow_proxy.py`) 以 Win32 `EnumWindows`（依 Z-Order）與 `GetWindowTextW` 直接讀取各 Chrome 視窗的即時標題（相容多設定檔後綴、音訊與高記憶體提示），在 `< 0.2ms` 內精準標記目前瀏覽器正在查看的分頁 (`viewing: true`) 與多視窗停留分頁 (`window_active: true`)。
  - 自動同步固定規則 `@chrome-tab-current`（`🟢 目前瀏覽中：<標題>`），在對話框直接輸入 `@current` 即可鎖定當下正在看的網頁。
- **零閃爍即時刷新（預設開啟）＋ 可記憶開關 ＋ 保留手動刷新 (`ui/chrome_tabs_picker.html`)**：
  - 新增雙層快取輪詢工具 `poll_chrome_tabs_app`（Win32 `< 1ms` 切換偵測 + 非阻塞背景 CDP 同步）與設定持久化工具 `set_chrome_tabs_prefs_app`（儲存於 `%LOCALAPPDATA%\antigravity-computer-use\ui_prefs.json`，不影響 Git 乾淨狀態）。
  - 側邊欄頂端同時提供 **`[● 即時]` 自動刷新切換鈕（預設開啟、跨重開記憶）** 與 **手動刷新旋轉按鈕**；前端採用狀態指紋差異比對（Fingerprint Diff），無變動時零 DOM 重繪、懸停與點擊絕不閃爍。

## [1.2.0] - 2026-11-25

### Added
- **背景無感自動更新（Silent Auto-Update）**：
  - 每次 Antigravity 啟動 `computer-use` MCP 時，代理層 (`bin/cua_window_glow_proxy.py`) 會延遲 4 秒於背景自動檢查 `origin/main` 並執行安全的 `git pull --ff-only`（預設每 6 小時檢查一次，不阻塞啟動、不彈黑窗）。
  - 內建多重保護護欄：僅在 `main` 分支且工作目錄無未提交修改時才會拉取更新；離線或有本機修改時靜默略過、絕不覆寫使用者修改。
  - 支援於 `mcp_config.json` 透過 `CUA_AUTO_UPDATE`（預設 `"true"`）與 `CUA_AUTO_UPDATE_INTERVAL_HOURS`（預設 `"6"`）客製開關與頻率。
  - 若背景自動拉取了新版程式，側邊欄 `Chrome Tabs` 底部狀態列會低調提示更新版本。

## [1.1.0] - 2026-11-25

### Changed
- **側邊欄「Chrome Tabs」介面全面重新設計 (`ui/chrome_tabs_picker.html`)**：
  - 採用 **Geist 極簡黑白灰（Monochrome）** 設計系統（深色／淺色自適應、切換主題時零閃爍動畫抑制），移除所有舊版霓虹青色膠囊標籤與重複邊框卡片。
  - **顯示真實網站 Logo 圖示 (`favicon`)**：代理層 (`bin/cua_window_glow_proxy.py`) 新增並行記憶體快取的 Favicon Data-URI 解析器，不受 iframe CSP 限制且對純黑透明背景 Logo 自動做深色模式反白對比保護；系統頁與 `localhost` 則搭配 `1.5px` 精準幾何向量圖示。
  - **單擊綁定 + 多選分頁 + 全鍵盤操控**：點擊整列即可一鍵切換綁定，點擊右側勾選圓圈（或 `Ctrl/Cmd + Click`）可同時綁定多個分頁至對話；支援 `/`（或 `Ctrl+K`）聚焦搜尋、`↑`/`↓`/`Enter`/`Esc` 鍵盤導覽與 Hover 複製 `@chrome-tab` 標籤。
  - 多個 Chrome 視窗開啟時自動顯示極簡視窗分組標題，並淨化離線狀態提示文字。

## [1.0.0] - 2026-11-22

### Added
- **背景桌面與 Chrome 操控整合 (`mcp_config.json`)**：整合 `cua-driver`（含 `--grant existing-profile`）與 `chrome-devtools-mcp`（`--autoConnect`），全程不搶實體滑鼠與前景視窗焦點。
- **分頁感知（Tab-Aware）同色圓角螢光外框代理 (`bin/cua_window_glow_proxy.py`)**：
  - 自動同步 `cua-driver` 虛擬游標顏色（如 `agy-cursor` → `#4CCCE0`），並與 `Cua.AgentCursorOverlay` 一併鎖定於目標視窗正上方一層 Z-Order。
  - 30 FPS 追蹤 Chrome 作用中分頁，切到目標分頁才顯示外框與虛擬游標、切走立即隱藏。
  - 內建 Windows Job Object 防孤兒行程機制。
- **聊天框 `@` 分頁即時同步**：自動將開啟中的 Chrome 分頁同步為 `~/.gemini/config/rules/chrome-tab-*.md`（`trigger: manual`，未選取時 0 Token），可於聊天框直接輸入 `@chrome-tab` 或關鍵字指定操作網頁。
- **Antigravity MCP App 側邊分頁選擇面板 (`ui/chrome_tabs_picker.html`)**：支援搜尋、重整與一鍵 `+ 加入對話框`（`updateModelContext()`）。
- **自動化部署與維護腳本 (`scripts/install.ps1`、`scripts/update.ps1`、`scripts/doctor.ps1`)** 與 Agent 專用自動安裝指南 (`INSTALL_FOR_AGENT.md`)。
- **內建安全規則與診斷技能 (`rules/AGENTS.md`、`skills/computer-use-setup/`、`skills/cua-driver/`)**。
