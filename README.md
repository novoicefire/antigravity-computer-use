<!-- Computer Use 外掛 GitHub 專案主說明書：提供完整安裝、自動部署、Chrome 設定、日常使用與更新排錯教學 -->
# Antigravity Computer Use Plugin

讓 **Google Antigravity** 在 Windows 上擁有像 OpenAI Codex Desktop 一樣的 **「不搶實體滑鼠、不搶前景焦點、背景操作指定 Chrome 分頁與桌面 App、同色圓角螢光外框 + 聊天框 `@` 與側邊欄分頁選擇器」** 完整電腦操控能力。

---

## 核心特色

- **不搶滑鼠、不搶焦點的背景操作**：透過 `cua-driver`（Windows UI Automation + Background `PostMessage` + Synthetic-Pen Injection）操作桌面軟體與視窗拖曳，你可以繼續工作完全不受干擾。
- **已登入 Chrome 分頁感知（Tab-Aware）背景操作與截圖**：透過 `existing-profile` 與 `--autoConnect` 直接操作你平常已登入的 Chrome（保留所有登入狀態、Cookie、擴充功能），即使你切換去看別的分頁或將其他視窗蓋在 Chrome 上方，AI 仍可在背景點擊、填表並透過 CDP 直接擷取該分頁畫面。
- **高 DPI 雙向圓角霓虹光暈 ＋ 60 FPS 跨分頁防漏守衛**：代理層 (`bin/cua_window_glow_proxy.py` 與 `bin/cua_devtools_glow_proxy.py`) 自動依螢幕 DPI（支援 2.8K / 4K 200% 縮放與最大化視窗）渲染同色圓角內外光暈（`agy-cursor` → `#4CCCE0`），嚴格釘在目標視窗正上方一層 Z-Order；並以 **60 FPS（`<16ms`）** 即時監控分頁標題，切走或開新分頁瞬間隱藏外框與虛擬游標、切回才浮現（若背景執行時視窗被蓋住，切回目標分頁才開始計算顯示秒數）。
- **Chrome 遠端桌面（`<canvas>`）與多視窗自動 CDP 救援**：內建零依賴本機 CDP WebSocket 救援通道 (`bin/cua_cdp_rescue.py`)，當多視窗觸發 `heuristic` 唯讀限制或操作 Chrome 遠端桌面等純 `<canvas>` 畫面時，自動無縫切換為背景像素座標點擊／拖曳／滾輪／鍵盤輸入與 `Page.captureScreenshot` 截圖。
- **聊天框 `@` 直接點選開啟中的網頁**：自動將 Chrome 分頁即時同步為 `trigger: manual` 規則（`~/.gemini/config/rules/chrome-tab-*.md`，未選取時 **0 Token**），輸入 `@current`、`@chrome-tab` 或關鍵字即可指定操作該分頁。
- **Antigravity 原生側邊欄「Chrome Tabs」面板 (MCP App)**：Geist 極簡黑白灰深淺自適應設計，顯示真實網站 Favicon、**`● 瀏覽中`** 即時標記與 **`[● 即時]`** 零閃爍自動刷新，單擊整列即可綁定分頁至對話框（支援多選與全鍵盤操作）。
- **檔案下載／上傳無痛處理**：支援透過 `browser_download` / `browser_set_input_files` 繞過檔案總管彈窗直接存取檔案，也支援直接背景操作 Windows 檔案總管與「另存新檔」對話框。
- **Git 驅動持續更新**：外掛本體就是 Git Repository，支援背景無感自動更新，任何電腦一條指令（或一句話請 Antigravity）即可同步最新功能。

---

## 快速安裝（二選一，皆為全自動）

> **系統需求**：Windows 10 / 11、Google Antigravity、Google Chrome（建議 144+）。缺少 Git / Python 3 / Node.js / `cua-driver` 時，安裝腳本會自動補齊。

### 方法一：直接貼一句話給 Antigravity 全自動安裝（最推薦）

在任意一台新電腦打開 **Google Antigravity**，直接貼上這句話：

```text
請閱讀 https://github.com/novoicefire/antigravity-computer-use 的 INSTALL_FOR_AGENT.md，依照裡面的步驟幫這台電腦自動安裝、設定並驗證 computer-use 外掛。
```

Antigravity 會自動幫你跑完相依套件安裝、Clone 外掛、環境健檢，並引導你完成最後一步 Chrome 開關。

### 方法二：PowerShell 一行指令安裝

打開 Windows PowerShell，直接貼上執行：

```powershell
irm https://raw.githubusercontent.com/novoicefire/antigravity-computer-use/main/scripts/install.ps1 | iex
```

腳本會自動完成：
1. 檢查並透過 `winget` 安裝 **Git**、**Python 3.12 (`py -3`)**、**Node.js LTS (`npx`)**（已安裝則自動略過）。
2. 安裝 **`cua-driver`**（自動設定 `CUA_DRIVER_NO_CONNECT=1` 非互動安裝）。
3. 將本專案 Clone 到 `%USERPROFILE%\.gemini\config\plugins\computer-use`（若已安裝則自動 `git pull` 更新）。
4. 執行 `scripts/doctor.ps1` 輸出完整環境健檢報告。

---

## 首次使用必做：開啟 Chrome 遠端偵錯（一次性設定，5 秒）

為了讓外掛能**直接操作你已經登入好的 Chrome 分頁**（無須另開空白瀏覽器或重新登入），請在平常使用的 Chrome 做一次以下設定：

1. 在 Chrome 網址列輸入並前往：`chrome://inspect/#remote-debugging`
2. 勾選 **Enable remote debugging（允許遠端偵錯）**
3. 確認 Antigravity 的 **Customizations → Plugins** 中 **`Computer Use (Background Desktop & Chrome)`** 已啟用（若是全新安裝，重新載入或重開一次 Antigravity 讓 MCP 啟動即可）。

> 若開啟 Chrome 遠端偵錯時 Chrome 跳出授權提示，按「允許」即可。

---

## 使用教學

### 1. 用 `@` 指定要讓 AI 操作的 Chrome 分頁
- 開著 Chrome 時，外掛會自動把各分頁同步進聊天框 `@` 選單，並即時維護一個指向當下瀏覽中分頁的 **`@chrome-tab-current`**。
- 在聊天框輸入 **`@current`**（直接指定目前正在看的網頁）、**`@chrome-tab`** 或網頁關鍵字（如 `@github`、`@youtube`），選中分頁後直接下指令：
  - 「幫我整理這個分頁的重點並填寫表單，不要搶我滑鼠」
  - 「在背景幫我把這個頁面的 PDF 下載到 `D:\Downloads`」

### 2. 用側邊欄「Chrome Tabs」面板即時檢視與挑選分頁
- 在 Antigravity 右側面板點擊 **`+` → `Chrome Tabs`**（或在對話中對 AI 說「打開 Chrome Tabs 選擇面板」）。
- **即時標記與自動刷新**：面板預設開啟 **`[● 即時]`** 自動刷新，你在 Chrome 切換分頁或新開網頁時，面板會自動無閃爍更新並以 **`● 瀏覽中`** 標示目前正查看的網頁；也可隨時點擊 **`[● 即時]`** 切換關閉（設定會自動記憶），並保留右上角**手動重新整理**按鈕。
- 點擊任一分頁列即可將該網頁綁定至對話框（點擊右側圓圈或 `Ctrl + Click` 支援多選綁定多個分頁）。

### 3. 背景操控 Windows 桌面軟體（含拖曳與檔案總管）
直接對 Antigravity 下指令即可，例如：
- 「在背景幫我打開小算盤算 `789 * 321`，不要切換我的前景視窗」
- 「幫我把 Chrome 的 YouTube 分頁從最左邊拖曳到最右邊」
- 「幫我在背景打開檔案總管查看 `D:\Projects` 裡的最新檔案」

### 4. 背景分頁截圖與 Chrome 遠端桌面最佳實務
- **切換分頁或視窗被蓋住都能截圖**：使用瀏覽器分頁工具（`get_browser_state(include_screenshot=true)`）時，影像直接取自 Chrome 內部 CDP 渲染管線，即使你正在看 YouTube 或全螢幕使用 Antigravity 把 Chrome 蓋在底下，AI 都能拍出清晰完整的目標分頁畫面。
- **「蓋在底下」優於「按 `—` 最小化」**：當需要頻繁擷取網頁畫面或透過 **Chrome 遠端桌面（`remotedesktop.google.com`）** 操控另一台電腦時，建議直接讓其他視窗蓋在 Chrome 上方即可，避免按右上角 `—` 縮進工作列（Windows 與 Chromium 對最小化視窗會啟動省電凍結，可能暫停 `<canvas>` 視訊流與新畫面繪製）。

---

## 持續更新與環境診斷

### 背景無感自動更新（預設啟用）
- 只要是透過上述安裝方式（Git 追蹤模式）安裝的電腦，每次啟動 Antigravity（以及持續執行每 6 小時），外掛會自動在背景檢查 `origin/main` 並執行 **`git pull --ff-only`**，全程不彈視窗、不影響啟動速度。
- **多重安全保護**：僅在 `main` 分支且無本機修改時才會自動拉取；若你有自訂修改或處於離線狀態會自動靜默略過。
- 若想關閉自動更新或調整間隔，可在 `mcp_config.json` 的 `env` 調整 `CUA_AUTO_UPDATE`（`"false"` 關閉）與 `CUA_AUTO_UPDATE_INTERVAL_HOURS`（預設 `"6"`）。

### 立即手動更新（或順便升級 `cua-driver`）
你可以直接對 Antigravity 說：
> 「幫我更新 `computer-use` 外掛」

或在 PowerShell 執行：
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\.gemini\config\plugins\computer-use\scripts\update.ps1"
```

如果也要順便把底層的 `cua-driver` 一起升級到官方最新版，加上 `-UpgradeDriver`：
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\.gemini\config\plugins\computer-use\scripts\update.ps1" -UpgradeDriver
```

> 若更新內容包含 `mcp_config.json` 或 `bin/*.py`，更新後在 Antigravity 外掛頁面將 `computer-use` 關閉再開一次（或重開 Antigravity）即可熱重載。

### 隨時執行環境健檢
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\.gemini\config\plugins\computer-use\scripts\doctor.ps1"
```

---

## 客製化設定 (`mcp_config.json`)

可在 `%USERPROFILE%\.gemini\config\plugins\computer-use\mcp_config.json` 的 `cua-driver.env`（及 `chrome-devtools.env`）調整外觀與行為：

| 環境變數 | 預設值 | 說明 |
| :--- | :--- | :--- |
| `CUA_GLOW_COLOR` | `"auto"` | 自動與 `cua-driver` 虛擬游標同色（`agy-cursor` → `#4CCCE0`），也可指定任意 `#RRGGBB` 色碼（如 `"#A855F7"`） |
| `CUA_GLOW_HOLD_SEC` | `"3.5"` | 桌面視窗最後一次操作後維持發光外框的秒數（Chrome 分頁模式會至少維持 `9.0` 秒，且支援背景遮擋「切回才開始倒數」） |
| `CUA_EXCLUDE_FROM_CAPTURE` | `"false"` | 預設允許螢幕截圖／錄影拍到圓角螢光外框與代理游標以便驗證；設為 `"true"` 則啟用 `WDA_EXCLUDEFROMCAPTURE` 於截圖中自動隱形 |
| `CUA_AUTO_UPDATE` | `"true"` | 啟動時背景自動 `git pull --ff-only` 更新外掛（設為 `"false"` 可關閉） |
| `CUA_AUTO_UPDATE_INTERVAL_HOURS` | `"6"` | 背景自動檢查更新的最小間隔小時數 |

> 單獨測試某個視窗 HWND 的圓角螢光外框效果：
> ```powershell
> py -3 "$env:USERPROFILE\.gemini\config\plugins\computer-use\bin\cua_window_glow_proxy.py" --test-hwnd <window_id> agy-cursor
> ```

---

## 專案結構

```text
antigravity-computer-use/
├── plugin.json                      # Antigravity 外掛清單與中繼資料 (v1.4.0)
├── mcp_config.json                  # cua-driver 與 chrome-devtools (雙通道螢光代理) MCP 設定
├── bin/
│   ├── cua_window_glow_proxy.py     # 高 DPI 圓角內外光暈、60 FPS 跨分頁防漏守衛、@ 分頁同步與自癒代理層
│   ├── cua_cdp_rescue.py            # 純標準庫 CDP WebSocket 救援通道 (<canvas>/遠端桌面座標操控與虛擬游標)
│   └── cua_devtools_glow_proxy.py   # chrome-devtools MCP 分頁感知圓角光暈與虛擬游標包裝代理
├── ui/
│   └── chrome_tabs_picker.html      # Antigravity MCP App 側邊欄分頁選擇器 (Geist 極簡主題 + 即時同步)
├── rules/
│   └── AGENTS.md                    # 背景優先、Z-Order 鎖定、Qt/LINE 鍵盤與外部動作安全規則
├── skills/
│   ├── computer-use-setup/SKILL.md  # 環境診斷、修復、版本更新技能
│   └── cua-driver/                  # cua-driver 完整桌面與瀏覽器操控手冊
├── scripts/
│   ├── install.ps1                  # 一鍵自動安裝腳本
│   ├── update.ps1                   # 一鍵持續更新腳本
│   └── doctor.ps1                   # 環境健檢與 Python 模組語法/載入驗證腳本
├── INSTALL_FOR_AGENT.md             # 專給其他電腦 Antigravity 執行的自動部署手冊
├── CHANGELOG.md                     # 版本異動紀錄
└── assets/logo.svg                  # 外掛圖示
```

---

## 常見問題排錯（FAQ）

1. **`chrome-devtools` 顯示 `Could not find DevToolsActivePort`**
   - 請確認已在 Chrome 打開 `chrome://inspect/#remote-debugging` 並勾選「允許遠端偵錯」，且 Chrome 正在執行中。
2. **需要操控「以系統管理員身分執行」的視窗**
   - 請以「系統管理員身分」開一次 PowerShell 執行：
     ```powershell
     cua-driver autostart enable
     cua-driver autostart kick
     ```
3. **`scripts/doctor.ps1` 提示找不到 `py -3`**
   - 代表系統裝的是不含 `py` launcher 的 Python（如 Microsoft Store 版或 Conda）。建議執行 `winget install --id Python.Python.3.12 -e` 補齊官方 Launcher，或暫時將 `mcp_config.json` 的 `"command": "py"` 與 `"-3"` 改為 `"command": "python"`。
4. **如何貢獻或二次開發？**
   - 直接在 `%USERPROFILE%\.gemini\config\plugins\computer-use` 修改並即時測試，確認無誤後即可 `git commit` / `git push` 或發起 Pull Request！

## License

[MIT](./LICENSE)
