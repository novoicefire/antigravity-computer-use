<!-- Computer Use 外掛版本異動紀錄：追蹤每次功能新增、修正與相容性改善 -->
# Changelog

All notable changes to **antigravity-computer-use** will be documented in this file.

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
