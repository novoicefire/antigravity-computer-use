# -*- coding: utf-8 -*-
# 檔案用途：cua-driver MCP 透明代理層（目標視窗游標同色圓角螢光外框 + Chrome 分頁感知 + @ 選單分頁同步 + MCP App 側邊分頁選擇器）
# 功能說明：
#   1. 自動追蹤被操作視窗與 Chrome 分頁，切到目標分頁才顯示同色圓角螢光外框與虛擬游標、切走瞬間自動隱藏。
#   2. 自動同步開啟中的 Chrome 分頁至 ~/.gemini/config/rules/chrome-tab-*.md (trigger: manual)，於聊天框輸入 @ 即可直接選取網頁。
#   3. 內建 Antigravity MCP App 側邊欄 (Chrome Tabs)，支援一鍵將指定分頁附加為聊天框 Context Pill。

from __future__ import annotations

import base64
import concurrent.futures
import ctypes
from ctypes import wintypes
import datetime
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# Win32 / DWM / Job Object Constants & Structures
# ---------------------------------------------------------------------------
WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000

WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

GWL_EXSTYLE = -20
GW_HWNDPREV = 3

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_HIDEWINDOW = 0x0080
SWP_NOOWNERZORDER = 0x0200

HWND_TOP = wintypes.HWND(0)
HWND_TOPMOST = wintypes.HWND(-1 & 0xFFFFFFFFFFFFFFFF)
HWND_NOTOPMOST = wintypes.HWND(-2 & 0xFFFFFFFFFFFFFFFF)

ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
DIB_RGB_COLORS = 0

DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_BORDER_COLOR = 34
DWMWA_COLOR_DEFAULT = 0xFFFFFFFF

WDA_EXCLUDEFROMCAPTURE = 0x00000011

WM_DESTROY = 0x0002
WM_TIMER = 0x0113

JobObjectExtendedLimitInformation = 9
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

CHROME_TABS_UI_URI = "ui://computer-use/chrome-tabs.html"
CHROME_TAB_RULE_PREFIX = "chrome-tab-"

# ---------------------------------------------------------------------------
# cua-driver 原生虛擬游標色盤與 Session 顏色解析 (100% 同步 cua-driver.exe)
# ---------------------------------------------------------------------------
CUA_DEFAULT_CURSOR_RGB = (0x5E, 0xC0, 0xE8)  # #5EC0E8 (default session)
CUA_SESSION_PALETTE: list[tuple[int, int, int]] = [
    (0xB2, 0x84, 0xFF),  # 0: #B284FF (Lavender Purple)
    (0xF7, 0x84, 0xAA),  # 1: #F784AA (Rose Pink)
    (0x60, 0xDA, 0xAE),  # 2: #60DAAE (Mint Green)
    (0xF4, 0xB2, 0x42),  # 3: #F4B242 (Amber Gold)
    (0x4C, 0xCC, 0xE0),  # 4: #4CCCE0 (Cyan Blue — "agy-cursor")
    (0xDD, 0x71, 0xEC),  # 5: #DD71EC (Orchid Magenta)
    (0xE8, 0x52, 0x62),  # 6: #E85262 (Coral Red)
    (0xB8, 0xDC, 0x36),  # 7: #B8DC36 (Lime Green)
    (0x50, 0x7E, 0xEC),  # 8: #507EEC (Royal Blue)
]


def resolve_cua_cursor_color(session_name: str | None) -> tuple[int, int, int]:
    """計算與 cua-driver.exe 完全一致的虛擬游標顏色 (支援 CUA_GLOW_COLOR 環境變數覆寫)。"""
    env_override = os.environ.get("CUA_GLOW_COLOR", "auto").strip()
    if env_override and env_override.lower() != "auto":
        hex_str = env_override.lstrip("#")
        if len(hex_str) == 6:
            try:
                return (int(hex_str[0:2], 16), int(hex_str[2:4], 16), int(hex_str[4:6], 16))
            except ValueError:
                pass

    if not session_name or session_name == "default":
        return CUA_DEFAULT_CURSOR_RGB

    trimmed = session_name.rstrip("-_ /")
    if not trimmed:
        return CUA_DEFAULT_CURSOR_RGB

    if len(trimmed) == 1 and trimmed.isascii() and trimmed.isalpha():
        idx = (ord(trimmed.lower()) - ord("a")) % len(CUA_SESSION_PALETTE)
        return CUA_SESSION_PALETTE[idx]

    m_num = re.search(r"(\d+)$", trimmed)
    if m_num:
        val = int(m_num.group(1))
        if val >= 1:
            return CUA_SESSION_PALETTE[(val - 1) % len(CUA_SESSION_PALETTE)]

    h = 0x811C9DC5
    for ch in trimmed:
        h = ((h ^ ord(ch)) * 0x01000193) & 0xFFFFFFFF
    return CUA_SESSION_PALETTE[h % len(CUA_SESSION_PALETTE)]


def discover_cua_driver_binary() -> str:
    """自動尋找本機 cua-driver.exe 路徑，避免硬編碼使用者名稱。"""
    candidates = [
        os.environ.get("CUA_DRIVER_BIN", ""),
        os.path.join(
            os.environ.get("LOCALAPPDATA", ""),
            "Programs",
            "Cua",
            "cua-driver",
            "bin",
            "cua-driver.exe",
        ),
        shutil.which("cua-driver") or "",
        os.path.join(
            os.path.expanduser("~"),
            ".cua-driver",
            "packages",
            "current",
            "cua-driver.exe",
        ),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return "cua-driver.exe"


def run_safe_auto_update(plugin_root: str, force: bool = False) -> dict:
    """安全背景自動更新（僅於 main 分支且無本機修改時執行 git pull --ff-only）。"""
    enabled = os.environ.get("CUA_AUTO_UPDATE", "true").strip().lower()
    if enabled in ("0", "false", "no", "off"):
        return {"status": "disabled"}

    if not os.path.isdir(os.path.join(plugin_root, ".git")):
        return {"status": "not_git"}

    git_bin = shutil.which("git")
    if not git_bin:
        return {"status": "no_git"}

    try:
        interval_hours = max(0.0, float(os.environ.get("CUA_AUTO_UPDATE_INTERVAL_HOURS", "6")))
    except ValueError:
        interval_hours = 6.0

    marker_path = os.path.join(tempfile.gettempdir(), "antigravity_computer_use_last_update_check.txt")
    now_ts = time.time()
    if not force and interval_hours > 0 and os.path.isfile(marker_path):
        try:
            age_sec = now_ts - os.path.getmtime(marker_path)
            if 0 <= age_sec < interval_hours * 3600.0:
                return {"status": "cooldown"}
        except OSError:
            pass

    git_env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    creation_flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW

    def _git(args: list[str], timeout: float = 12.0) -> subprocess.CompletedProcess | None:
        try:
            return subprocess.run(
                [git_bin, "-C", plugin_root, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=git_env,
                creationflags=creation_flags,
            )
        except Exception:
            return None

    r_branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], timeout=5.0)
    if not r_branch or r_branch.returncode != 0:
        return {"status": "git_error"}
    branch = (r_branch.stdout or "").strip()
    if branch != "main":
        return {"status": "skipped_branch", "branch": branch}

    r_dirty = _git(["status", "--porcelain"], timeout=5.0)
    if not r_dirty or r_dirty.returncode != 0:
        return {"status": "git_error"}
    if (r_dirty.stdout or "").strip():
        return {"status": "skipped_dirty"}

    r_head = _git(["rev-parse", "--short", "HEAD"], timeout=5.0)
    before_rev = (r_head.stdout or "").strip() if r_head and r_head.returncode == 0 else ""

    r_fetch = _git(["fetch", "--quiet", "--tags", "origin"], timeout=12.0)
    if not r_fetch or r_fetch.returncode != 0:
        return {"status": "offline"}

    try:
        with open(marker_path, "w", encoding="utf-8") as mf:
            mf.write(f"{int(now_ts)}\n")
    except OSError:
        pass

    r_count = _git(["rev-list", "--count", "HEAD..origin/main"], timeout=5.0)
    try:
        behind = int((r_count.stdout or "0").strip()) if r_count and r_count.returncode == 0 else 0
    except ValueError:
        behind = 0

    if behind <= 0:
        return {"status": "up_to_date", "rev": before_rev}

    r_diff = _git(["diff", "--name-only", "HEAD..origin/main"], timeout=5.0)
    changed_files = [
        line.strip()
        for line in ((r_diff.stdout or "").splitlines() if r_diff and r_diff.returncode == 0 else [])
        if line.strip()
    ]

    r_pull = _git(["pull", "--ff-only", "--quiet", "origin", "main"], timeout=15.0)
    if not r_pull or r_pull.returncode != 0:
        return {"status": "diverged"}

    r_after = _git(["rev-parse", "--short", "HEAD"], timeout=5.0)
    after_rev = (r_after.stdout or "").strip() if r_after and r_after.returncode == 0 else ""
    restart_needed = any(
        f in ("bin/cua_window_glow_proxy.py", "mcp_config.json") for f in changed_files
    )
    return {
        "status": "updated",
        "from_rev": before_rev,
        "to_rev": after_rev,
        "changed_files": changed_files,
        "restart_needed": restart_needed,
    }


def normalize_browser_title(title: str) -> str:
    """正規化瀏覽器視窗／分頁標題，移除瀏覽器後綴與未讀數字前綴以便精準比對。"""
    if not title:
        return ""
    s = title.strip()
    for suffix in (
        " - Google Chrome",
        " - Microsoft Edge",
        " - Brave",
        " - Chromium",
        " - Arc",
    ):
        if s.lower().endswith(suffix.lower()):
            s = s[: -len(suffix)].strip()
            break
    s = re.sub(r"^\(\d+\+?\)\s*", "", s).strip()
    return s.lower()


def make_tab_rule_slug(index: int, title: str, url: str) -> str:
    """產生乾淨且易於在 @ 選單搜尋的分頁規則名稱 (e.g. chrome-tab-1-antigravity-github)。"""
    host = ""
    try:
        parsed = urllib.parse.urlparse(url or "")
        host = (parsed.hostname or "").replace("www.", "")
    except Exception:
        pass
    # 取簡短主網域名稱 (例如 github.com -> github, youtube.com -> youtube)
    short_host = host.split(".")[0] if host else ""
    short_host = re.sub(r"[^a-zA-Z0-9]+", "", short_host).lower()[:14]

    clean_title = normalize_browser_title(title or "")
    title_slug = re.sub(r"[^\w\u4e00-\u9fff]+", "-", clean_title).strip("-")[:24]

    parts = [f"chrome-tab-{index}"]
    if title_slug:
        parts.append(title_slug)
    if short_host and short_host not in title_slug.lower() and len(short_host) >= 2:
        parts.append(short_host)
    name = "-".join(parts)
    name = re.sub(r"-{2,}", "-", name).strip("-")
    return name[:52]


# 外框幾何與動畫參數
GLOW_SPREAD = 10
CORE_THICKNESS = 3
CORNER_RADIUS = 8
TOTAL_PAD = GLOW_SPREAD + CORE_THICKNESS
IDLE_HOLD_SEC = float(os.environ.get("CUA_GLOW_HOLD_SEC", "3.5"))
BROWSER_TAB_HOLD_SEC = max(IDLE_HOLD_SEC, 8.0)
FADE_OUT_SEC = 0.6


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32
dwmapi = ctypes.windll.dwmapi

# Explicit 64-bit Win32 Signatures
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE

kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
kernel32.CreateJobObjectW.restype = wintypes.HANDLE

kernel32.SetInformationJobObject.argtypes = [
    wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD
]
kernel32.SetInformationJobObject.restype = wintypes.BOOL

kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
kernel32.AssignProcessToJobObject.restype = wintypes.BOOL

user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t

user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
user32.CreateWindowExW.restype = wintypes.HWND

user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowExW.restype = wintypes.HWND

user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND

user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long

user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int

user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT
]
user32.SetWindowPos.restype = wintypes.BOOL

user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC

user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ReleaseDC.restype = ctypes.c_int

user32.UpdateLayeredWindow.argtypes = [
    wintypes.HWND, wintypes.HDC, ctypes.POINTER(POINT), ctypes.POINTER(SIZE),
    wintypes.HDC, ctypes.POINTER(POINT), wintypes.COLORREF,
    ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD,
]
user32.UpdateLayeredWindow.restype = wintypes.BOOL

gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC

gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP

gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ

gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL

gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.restype = wintypes.BOOL


def bind_process_to_kill_on_close_job(proc_handle: int) -> wintypes.HANDLE | None:
    """將子程序綁定至 Windows Job Object，確保父程序結束時自動清除子程序，防止孤兒行程。"""
    try:
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            return None
        kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(proc_handle))
        return job
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 目標視窗圓角螢光外框與 Tab-Aware / Z-Order 圖層同步管理器
# ---------------------------------------------------------------------------
class WindowGlowOverlay:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._target_hwnd: int = 0
        self._expected_tab_title: str = ""
        self._hold_sec: float = IDLE_HOLD_SEC
        self._last_active_ts: float = 0.0
        self._prev_dwm_hwnd: int = 0
        self._dwm_colored_hwnd: int = 0
        self._glow_hwnd: int = 0
        self._color_rgb: tuple[int, int, int] = resolve_cua_cursor_color("agy-cursor")
        self._last_cache_key: tuple[int, int, tuple[int, int, int]] = (0, 0, (0, 0, 0))
        self._dib_hdc = None
        self._dib_bmp = None
        self._dib_old = None
        self._dib_bits = ctypes.c_void_p()
        self._running = True
        self._thread = threading.Thread(target=self._ui_thread, daemon=True)
        self._thread.start()

    def highlight_window(
        self,
        hwnd: int,
        session_name: str | None = "agy-cursor",
        expected_tab_title: str = "",
    ) -> None:
        if not hwnd or hwnd <= 0:
            return
        rgb = resolve_cua_cursor_color(session_name)
        with self._lock:
            if not user32.IsWindow(wintypes.HWND(hwnd)):
                return
            if self._prev_dwm_hwnd and self._prev_dwm_hwnd != hwnd:
                self._restore_dwm_border(self._prev_dwm_hwnd)
                self._dwm_colored_hwnd = 0
            self._target_hwnd = hwnd
            self._expected_tab_title = expected_tab_title.strip()
            self._hold_sec = BROWSER_TAB_HOLD_SEC if self._expected_tab_title else IDLE_HOLD_SEC
            self._color_rgb = rgb
            self._last_active_ts = time.monotonic()
            self._prev_dwm_hwnd = hwnd

    def shutdown(self) -> None:
        self._running = False
        with self._lock:
            if self._prev_dwm_hwnd:
                self._restore_dwm_border(self._prev_dwm_hwnd)
                self._prev_dwm_hwnd = 0
                self._dwm_colored_hwnd = 0

    @staticmethod
    def _get_window_title(hwnd: int) -> str:
        buf = ctypes.create_unicode_buffer(512)
        n = user32.GetWindowTextW(wintypes.HWND(hwnd), buf, 512)
        return buf.value if n > 0 else ""

    @classmethod
    def _is_expected_tab_active(cls, hwnd: int, expected_tab_title: str) -> bool:
        """比對目前 Chrome 視窗標題是否與 AI 正在操作的分頁標題一致。"""
        if not expected_tab_title:
            return True
        live_title = cls._get_window_title(hwnd)
        norm_live = normalize_browser_title(live_title)
        norm_exp = normalize_browser_title(expected_tab_title)
        if not norm_exp or not norm_live:
            return False
        if norm_live == norm_exp:
            return True
        # 容許長標題截斷（前 24 字元前綴相符）
        prefix_len = min(24, len(norm_live), len(norm_exp))
        if prefix_len >= 6 and norm_live[:prefix_len] == norm_exp[:prefix_len]:
            return True
        return False

    @staticmethod
    def _set_dwm_border(hwnd: int, colorref: int) -> None:
        try:
            val = ctypes.c_uint32(colorref)
            dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd),
                DWMWA_BORDER_COLOR,
                ctypes.byref(val),
                ctypes.sizeof(val),
            )
        except Exception:
            pass

    def _restore_dwm_border(self, hwnd: int) -> None:
        if user32.IsWindow(wintypes.HWND(hwnd)):
            self._set_dwm_border(hwnd, DWMWA_COLOR_DEFAULT)

    @staticmethod
    def _get_target_bounds(hwnd: int) -> tuple[int, int, int, int] | None:
        rect = RECT()
        hr = dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd),
            DWMWA_EXTENDED_FRAME_BOUNDS,
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
        if hr != 0:
            if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
                return None
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w <= 10 or h <= 10:
            return None
        return (rect.left, rect.top, w, h)

    def _find_cua_cursor_overlays(self) -> list[int]:
        found: list[int] = []
        curr = user32.FindWindowExW(None, None, "Cua.AgentCursorOverlay", None)
        while curr:
            found.append(int(curr))
            curr = user32.FindWindowExW(None, wintypes.HWND(curr), "Cua.AgentCursorOverlay", None)
        return found

    def _hide_cursor_overlays_for_unselected_tab(self) -> None:
        """當使用者切去同個 Chrome 視窗的別的分頁時，瞬間隱藏殘留的虛擬游標避免干擾。"""
        for chwnd in self._find_cua_cursor_overlays():
            if user32.IsWindowVisible(wintypes.HWND(chwnd)):
                user32.ShowWindow(wintypes.HWND(chwnd), 0)

    def _pin_z_order_above_target(self, target_hwnd: int) -> None:
        """將螢光外框與 cua-driver 虛擬游標嚴格釘在 target_hwnd 的正上方一層 (並壓在前景視窗底下)。"""
        if not self._glow_hwnd or not user32.IsWindow(wintypes.HWND(target_hwnd)):
            return

        cursor_hwnds = set(self._find_cua_cursor_overlays())
        our_hwnds = cursor_hwnds | {self._glow_hwnd}

        prev_hwnd = user32.GetWindow(wintypes.HWND(target_hwnd), GW_HWNDPREV)
        while prev_hwnd and int(prev_hwnd) in our_hwnds:
            prev_hwnd = user32.GetWindow(wintypes.HWND(prev_hwnd), GW_HWNDPREV)

        target_ex = user32.GetWindowLongW(wintypes.HWND(target_hwnd), GWL_EXSTYLE)
        target_is_topmost = bool(target_ex & WS_EX_TOPMOST)

        insert_after = HWND_TOP
        if prev_hwnd:
            prev_ex = user32.GetWindowLongW(wintypes.HWND(prev_hwnd), GWL_EXSTYLE)
            prev_is_topmost = bool(prev_ex & WS_EX_TOPMOST)
            if target_is_topmost == prev_is_topmost:
                insert_after = wintypes.HWND(int(prev_hwnd))

        if not target_is_topmost:
            glow_ex = user32.GetWindowLongW(wintypes.HWND(self._glow_hwnd), GWL_EXSTYLE)
            if glow_ex & WS_EX_TOPMOST:
                user32.SetWindowPos(
                    wintypes.HWND(self._glow_hwnd),
                    HWND_NOTOPMOST,
                    0, 0, 0, 0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER,
                )
            for chwnd in cursor_hwnds:
                c_ex = user32.GetWindowLongW(wintypes.HWND(chwnd), GWL_EXSTYLE)
                if c_ex & WS_EX_TOPMOST:
                    user32.SetWindowPos(
                        wintypes.HWND(chwnd),
                        HWND_NOTOPMOST,
                        0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER,
                    )

        user32.SetWindowPos(
            wintypes.HWND(self._glow_hwnd),
            insert_after,
            0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER,
        )

        for chwnd in cursor_hwnds:
            user32.SetWindowPos(
                wintypes.HWND(chwnd),
                insert_after,
                0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER,
            )

    @staticmethod
    def _premul_pixel_bytes(d_from_outer: float, rgb: tuple[int, int, int]) -> bytes:
        if d_from_outer < 0.0 or d_from_outer >= TOTAL_PAD:
            return b"\x00\x00\x00\x00"
        nr, ng, nb = rgb
        if d_from_outer < GLOW_SPREAD:
            ratio = (d_from_outer + 1.0) / float(GLOW_SPREAD)
            alpha = int(145 * (ratio ** 1.75))
            r, g, b = nr, ng, nb
        else:
            alpha = 245
            r = min(255, nr + 85)
            g = min(255, ng + 65)
            b = min(255, nb + 65)
        pr = (r * alpha) // 255
        pg = (g * alpha) // 255
        pb = (b * alpha) // 255
        return struct.pack("<BBBB", pb, pg, pr, alpha)

    def _ensure_dib(self, width: int, height: int, rgb: tuple[int, int, int]) -> None:
        cache_key = (width, height, rgb)
        if self._last_cache_key == cache_key and self._dib_hdc and self._dib_bits:
            return
        self._free_dib()

        screen_dc = user32.GetDC(None)
        self._dib_hdc = gdi32.CreateCompatibleDC(screen_dc)
        user32.ReleaseDC(None, screen_dc)

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = width
        bmi.bmiHeader.biHeight = -height
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = DIB_RGB_COLORS

        bits = ctypes.c_void_p()
        self._dib_bmp = gdi32.CreateDIBSection(
            self._dib_hdc,
            ctypes.byref(bmi),
            DIB_RGB_COLORS,
            ctypes.byref(bits),
            None,
            0,
        )
        self._dib_old = gdi32.SelectObject(self._dib_hdc, self._dib_bmp)
        self._dib_bits = bits
        self._last_cache_key = cache_key

        stride = width * 4
        raw = bytearray(stride * height)
        px_lut = [self._premul_pixel_bytes(float(d), rgb) for d in range(TOTAL_PAD)]

        corner_box = TOTAL_PAD + CORNER_RADIUS
        straight_x0 = corner_box
        straight_x1 = max(straight_x0, width - corner_box)
        straight_len = straight_x1 - straight_x0

        if straight_len > 0:
            for d in range(TOTAL_PAD):
                p4 = px_lut[d]
                seg = p4 * straight_len
                top_start = d * stride + straight_x0 * 4
                bot_start = (height - 1 - d) * stride + straight_x0 * 4
                raw[top_start : top_start + straight_len * 4] = seg
                raw[bot_start : bot_start + straight_len * 4] = seg

        mid_y0 = corner_box
        mid_y1 = max(mid_y0, height - corner_box)
        if mid_y1 > mid_y0:
            mid_row = bytearray(stride)
            for d in range(TOTAL_PAD):
                p4 = px_lut[d]
                mid_row[d * 4 : (d + 1) * 4] = p4
                rx = width - 1 - d
                mid_row[rx * 4 : (rx + 1) * 4] = p4
            mid_row_bytes = bytes(mid_row)
            for y in range(mid_y0, mid_y1):
                off = y * stride
                raw[off : off + stride] = mid_row_bytes

        inner_r = float(CORNER_RADIUS)
        for cy in range(min(corner_box, height // 2)):
            for cx in range(min(corner_box, width // 2)):
                dx = float(corner_box - 1 - cx)
                dy = float(corner_box - 1 - cy)
                dist_from_center = math.hypot(dx, dy)
                offset_from_inner = dist_from_center - inner_r
                d_from_outer = float(TOTAL_PAD - 1) - offset_from_inner
                if 0.0 <= d_from_outer < float(TOTAL_PAD):
                    p4 = self._premul_pixel_bytes(d_from_outer, rgb)
                    tl_off = cy * stride + cx * 4
                    raw[tl_off : tl_off + 4] = p4
                    tr_off = cy * stride + (width - 1 - cx) * 4
                    raw[tr_off : tr_off + 4] = p4
                    bl_off = (height - 1 - cy) * stride + cx * 4
                    raw[bl_off : bl_off + 4] = p4
                    br_off = (height - 1 - cy) * stride + (width - 1 - cx) * 4
                    raw[br_off : br_off + 4] = p4

        c_buf = (ctypes.c_char * len(raw)).from_buffer(raw)
        ctypes.memmove(self._dib_bits, ctypes.addressof(c_buf), len(raw))

    def _free_dib(self) -> None:
        if self._dib_hdc:
            if self._dib_old:
                gdi32.SelectObject(self._dib_hdc, self._dib_old)
                self._dib_old = None
            if self._dib_bmp:
                gdi32.DeleteObject(self._dib_bmp)
                self._dib_bmp = None
            gdi32.DeleteDC(self._dib_hdc)
            self._dib_hdc = None
        self._dib_bits = ctypes.c_void_p()
        self._last_cache_key = (0, 0, (0, 0, 0))

    def _tick(self) -> None:
        with self._lock:
            target = self._target_hwnd
            last_ts = self._last_active_ts
            rgb = self._color_rgb
            exp_tab_title = self._expected_tab_title
            hold_sec = self._hold_sec

        if not target or not self._glow_hwnd:
            return

        now = time.monotonic()
        elapsed = now - last_ts

        # 超過停留時間或視窗不存在／已最小化 -> 結束高亮
        if (
            not user32.IsWindow(wintypes.HWND(target))
            or not user32.IsWindowVisible(wintypes.HWND(target))
            or user32.IsIconic(wintypes.HWND(target))
            or elapsed > (hold_sec + FADE_OUT_SEC)
        ):
            user32.ShowWindow(wintypes.HWND(self._glow_hwnd), 0)
            with self._lock:
                if self._dwm_colored_hwnd:
                    self._restore_dwm_border(self._dwm_colored_hwnd)
                    self._dwm_colored_hwnd = 0
                if elapsed > (hold_sec + FADE_OUT_SEC):
                    self._target_hwnd = 0
                    self._expected_tab_title = ""
            return

        # Tab-Aware 分頁感知檢查：如果目標是 Chrome 分頁，但使用者已切去別的分頁 -> 瞬間隱藏外框與殘留游標！
        if exp_tab_title and not self._is_expected_tab_active(target, exp_tab_title):
            if user32.IsWindowVisible(wintypes.HWND(self._glow_hwnd)):
                user32.ShowWindow(wintypes.HWND(self._glow_hwnd), 0)
            with self._lock:
                if self._dwm_colored_hwnd == target:
                    self._restore_dwm_border(target)
                    self._dwm_colored_hwnd = 0
            self._hide_cursor_overlays_for_unselected_tab()
            return

        bounds = self._get_target_bounds(target)
        if not bounds:
            user32.ShowWindow(wintypes.HWND(self._glow_hwnd), 0)
            return

        # 同步套用 Win11 DWM 同色邊框
        colorref = rgb[0] | (rgb[1] << 8) | (rgb[2] << 16)
        with self._lock:
            if self._dwm_colored_hwnd != target:
                self._set_dwm_border(target, colorref)
                self._dwm_colored_hwnd = target

        tx, ty, tw, th = bounds
        ox = tx - GLOW_SPREAD
        oy = ty - GLOW_SPREAD
        ow = tw + 2 * GLOW_SPREAD
        oh = th + 2 * GLOW_SPREAD

        self._ensure_dib(ow, oh, rgb)

        if elapsed <= hold_sec:
            pulse = 0.85 + 0.15 * math.sin(now * 6.0)
            master_alpha = int(255 * pulse)
        else:
            fade_progress = (elapsed - hold_sec) / FADE_OUT_SEC
            master_alpha = max(0, min(255, int(255 * (1.0 - fade_progress))))

        pt_dst = POINT(ox, oy)
        sz = SIZE(ow, oh)
        pt_src = POINT(0, 0)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, master_alpha, AC_SRC_ALPHA)

        screen_dc = user32.GetDC(None)
        user32.UpdateLayeredWindow(
            wintypes.HWND(self._glow_hwnd),
            screen_dc,
            ctypes.byref(pt_dst),
            ctypes.byref(sz),
            self._dib_hdc,
            ctypes.byref(pt_src),
            0,
            ctypes.byref(blend),
            ULW_ALPHA,
        )
        user32.ReleaseDC(None, screen_dc)

        if not user32.IsWindowVisible(wintypes.HWND(self._glow_hwnd)):
            user32.ShowWindow(wintypes.HWND(self._glow_hwnd), 4)  # SW_SHOWNOACTIVATE

        self._pin_z_order_above_target(target)

    def _ui_thread(self) -> None:
        try:
            user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:
            pass

        def _wnd_proc(hwnd, msg, wparam, lparam):
            if msg == WM_TIMER:
                try:
                    self._tick()
                except Exception:
                    pass
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wnd_proc_ref = WNDPROC(_wnd_proc)
        hinst = kernel32.GetModuleHandleW(None)
        class_name = "Cua.TargetWindowGlowBorder"

        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wnd_proc_ref
        wc.hInstance = hinst
        wc.lpszClassName = class_name
        user32.RegisterClassExW(ctypes.byref(wc))

        ex_style = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        hwnd = user32.CreateWindowExW(
            ex_style,
            class_name,
            "Cua.TargetWindowGlowBorder",
            WS_POPUP,
            0, 0, 10, 10,
            None, None, hinst, None,
        )
        self._glow_hwnd = int(hwnd) if hwnd else 0

        if self._glow_hwnd:
            try:
                user32.SetWindowDisplayAffinity(wintypes.HWND(self._glow_hwnd), WDA_EXCLUDEFROMCAPTURE)
            except Exception:
                pass
            user32.SetTimer(wintypes.HWND(self._glow_hwnd), 1, 33, None)

        msg = wintypes.MSG()
        while self._running and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        self._free_dib()


# ---------------------------------------------------------------------------
# MCP JSON-RPC 代理 + @ 規則同步器 + Chrome Tabs Picker MCP App 伺服器
# ---------------------------------------------------------------------------
class CuaMcpGlowProxy:
    def __init__(self, cua_binary: str, cua_args: list[str]) -> None:
        self.cua_binary = cua_binary
        self.cua_args = cua_args
        self.overlay = WindowGlowOverlay()

        self.snapshot_to_hwnd: dict[str, int] = {}
        self.pid_to_hwnd: dict[int, int] = {}
        self.browser_target_to_hwnd: dict[str, int] = {}
        self.browser_target_to_pid: dict[str, int] = {}
        self.tab_id_to_info: dict[str, dict] = {}
        self.req_id_to_meta: dict[object, dict] = {}
        self.active_session: str = "agy-cursor"

        self._state_lock = threading.Lock()
        self._stdin_lock = threading.Lock()
        self._stdout_lock = threading.Lock()
        self._sync_lock = threading.Lock()

        self._proc: subprocess.Popen | None = None
        self._job_handle = None
        self._running = True
        self._initialized = False
        self._client_inflight = 0

        self._internal_seq = 0
        self._internal_pending: dict[str, tuple[threading.Event, list]] = {}
        self._internal_lock = threading.Lock()
        self._last_client_meta: dict | None = None

        self._rules_dir = os.path.expanduser("~/.gemini/config/rules")
        self._plugin_root = os.path.normpath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
        )
        self._ui_html_path = os.path.join(self._plugin_root, "ui", "chrome_tabs_picker.html")
        self._favicon_cache: dict[str, tuple[str, float]] = {}
        self._favicon_lock = threading.Lock()
        self._auto_update_result: dict | None = None

    # -- Safe stdio helpers -------------------------------------------------
    def _send_to_client(self, obj: dict) -> None:
        raw = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        with self._stdout_lock:
            sys.stdout.buffer.write(raw)
            sys.stdout.buffer.flush()

    def _write_raw_to_client(self, line: bytes) -> None:
        with self._stdout_lock:
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()

    def _send_to_upstream(self, raw_line: bytes) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        with self._stdin_lock:
            self._proc.stdin.write(raw_line)
            self._proc.stdin.flush()

    def _internal_call_tool(self, tool_name: str, arguments: dict, timeout: float = 8.0) -> dict | None:
        """代理層內部呼叫子程序 cua-driver.exe（結果會被攔截，不會轉發給 Antigravity）。"""
        if not self._proc or not self._proc.stdin:
            return None
        with self._internal_lock:
            self._internal_seq += 1
            req_id = f"__glow_proxy_{self._internal_seq}"
            ev = threading.Event()
            holder: list = []
            self._internal_pending[req_id] = (ev, holder)

        req_params: dict = {"name": tool_name, "arguments": arguments}
        with self._state_lock:
            saved_meta = dict(self._last_client_meta) if isinstance(self._last_client_meta, dict) else None
        if saved_meta:
            req_params["_meta"] = saved_meta

        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/call",
            "params": req_params,
        }
        try:
            self._send_to_upstream((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
            if ev.wait(timeout=timeout) and holder:
                return holder[0]
        except Exception:
            pass
        finally:
            with self._internal_lock:
                self._internal_pending.pop(req_id, None)
        return None

    # -- @ 選單規則同步 (~/.gemini/config/rules/chrome-tab-*.md) -------------
    def _sync_chrome_tab_rules(self, tabs_list: list[dict]) -> int:
        """將開啟中的 Chrome 分頁同步為 trigger: manual 規則檔，支援聊天框直接 @ 選取且不選取時 0 Token。"""
        try:
            os.makedirs(self._rules_dir, exist_ok=True)
            desired_files: dict[str, str] = {}
            used_names: set[str] = set()

            for idx, tab in enumerate(tabs_list, start=1):
                title = (tab.get("title") or "未命名分頁").strip()
                url = (tab.get("url") or "").strip()
                base_name = make_tab_rule_slug(idx, title, url)
                rule_name = base_name
                suffix = 2
                while rule_name in used_names:
                    rule_name = f"{base_name}-{suffix}"
                    suffix += 1
                used_names.add(rule_name)
                tab["rule_name"] = rule_name

                safe_desc_title = title.replace('"', "'").replace("\n", " ")[:70]
                safe_desc_url = url.replace('"', "'").replace("\n", " ")[:90]
                content = (
                    "---\n"
                    "trigger: manual\n"
                    f'description: "🌐 Chrome 分頁：{safe_desc_title} ({safe_desc_url})"\n'
                    "---\n"
                    "<!-- 自動同步之 Chrome 分頁參照規則 (trigger: manual，僅於手動 @ 選取時載入) -->\n"
                    "請使用 `computer-use` 插件的 `cua-driver` 瀏覽器工具在背景操作以下指定 Chrome 分頁"
                    "（不要切走使用者目前正在看的分頁；使用者切回此分頁時會自動顯示同色外框與游標，切走時自動隱藏）：\n"
                    f"- **分頁標題**：{title}\n"
                    f"- **分頁網址**：{url}\n"
                    f"- **Chrome 視窗**：`pid={tab.get('pid')}`, `window_id={tab.get('window_id')}`\n"
                    f"- **綁定識別碼**：`target_id={tab.get('target_id')}`, `tab_id={tab.get('tab_id')}`\n"
                )
                desired_files[f"{rule_name}.md"] = content

            # 寫入或更新現有分頁規則（內容未變就不重複寫碟）
            for fname, content in desired_files.items():
                fpath = os.path.join(self._rules_dir, fname)
                need_write = True
                if os.path.isfile(fpath):
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            if f.read() == content:
                                need_write = False
                    except Exception:
                        pass
                if need_write:
                    with open(fpath, "w", encoding="utf-8") as f:
                        f.write(content)

            # 清理已關閉的舊分頁規則
            for existing in os.listdir(self._rules_dir):
                if existing.startswith(CHROME_TAB_RULE_PREFIX) and existing.endswith(".md"):
                    if existing not in desired_files:
                        try:
                            os.remove(os.path.join(self._rules_dir, existing))
                        except Exception:
                            pass

            return len(desired_files)
        except Exception:
            return 0

    def _sync_all_chrome_tabs(self) -> dict:
        """透過已授權的 cua-driver 內部通道抓取所有開啟中的 Chrome 分頁並同步至 @ 規則與面板。"""
        with self._sync_lock:
            now_str = datetime.datetime.now().strftime("%H:%M:%S")
            collected_tabs: list[dict] = []
            last_error = ""

            lw_resp = self._internal_call_tool(
                "list_windows",
                {"app": "chrome", "on_screen_only": False, "session": self.active_session},
                timeout=6.0,
            )
            if not lw_resp:
                return {
                    "tabs": [],
                    "synced_rules_count": 0,
                    "updated_at": now_str,
                    "error": "無法連線至 cua-driver 列出 Chrome 視窗。",
                }

            res_obj = lw_resp.get("result") or {}
            sc = res_obj.get("structuredContent") if isinstance(res_obj, dict) else None
            windows: list[dict] = []
            if isinstance(sc, dict):
                cand = sc.get("windows") or sc.get("_legacy_windows") or []
                if isinstance(cand, list):
                    for w in cand:
                        if isinstance(w, dict) and w.get("pid") and (w.get("window_id") or w.get("id")):
                            windows.append(
                                {
                                    "pid": int(w["pid"]),
                                    "window_id": int(w.get("window_id") or w.get("id")),
                                }
                            )

            if not windows:
                c_list = res_obj.get("content") if isinstance(res_obj, dict) else []
                txt = "\n".join(
                    c.get("text", "") for c in (c_list or []) if isinstance(c, dict)
                )
                for line in txt.splitlines():
                    m = re.search(r"\(pid\s+(\d+)\).*?\[window_id:\s*(\d+)\]", line)
                    if m:
                        windows.append({"pid": int(m.group(1)), "window_id": int(m.group(2))})

            if not windows:
                with self._state_lock:
                    seen_pairs: set[tuple[int, int]] = set()
                    for info in self.tab_id_to_info.values():
                        p_val = info.get("pid")
                        w_val = info.get("window_id")
                        if isinstance(p_val, int) and isinstance(w_val, int) and p_val > 0 and w_val > 0:
                            if (p_val, w_val) not in seen_pairs:
                                seen_pairs.add((p_val, w_val))
                                windows.append({"pid": p_val, "window_id": w_val})

            if not windows:
                return {
                    "tabs": [],
                    "window_count": 0,
                    "synced_rules_count": 0,
                    "updated_at": now_str,
                    "error": "目前沒有偵測到開啟中的 Google Chrome 視窗。",
                }

            for win_idx, win in enumerate(windows, start=1):
                pid = win.get("pid")
                wid = win.get("window_id")
                if not isinstance(pid, int) or not isinstance(wid, int):
                    continue

                bs_resp = self._internal_call_tool(
                    "get_browser_state",
                    {"pid": pid, "window_id": wid, "session": self.active_session},
                    timeout=6.0,
                )
                bs_res = (bs_resp or {}).get("result") or {}
                bs_sc = bs_res.get("structuredContent") if isinstance(bs_res, dict) else None

                # 若尚未綁定 existing_profile，嘗試呼叫一次 browser_prepare
                if not (isinstance(bs_sc, dict) and bs_sc.get("status") == "ok"):
                    self._internal_call_tool(
                        "browser_prepare",
                        {
                            "pid": pid,
                            "window_id": wid,
                            "session": self.active_session,
                            "strategy": {"kind": "existing_profile"},
                        },
                        timeout=8.0,
                    )
                    bs_resp = self._internal_call_tool(
                        "get_browser_state",
                        {"pid": pid, "window_id": wid, "session": self.active_session},
                        timeout=6.0,
                    )
                    bs_res = (bs_resp or {}).get("result") or {}
                    bs_sc = bs_res.get("structuredContent") if isinstance(bs_res, dict) else None

                if isinstance(bs_sc, dict) and isinstance(bs_sc.get("tabs"), list):
                    tid = bs_sc.get("target_id") or ""
                    with self._state_lock:
                        if tid:
                            self.browser_target_to_hwnd[tid] = wid
                            self.browser_target_to_pid[tid] = pid
                    for t in bs_sc["tabs"]:
                        if not isinstance(t, dict):
                            continue
                        tab_id = t.get("tab_id") or ""
                        info = {
                            "tab_id": tab_id,
                            "target_id": tid,
                            "pid": pid,
                            "window_id": wid,
                            "window_index": win_idx,
                            "title": t.get("title") or "",
                            "url": t.get("url") or "",
                            "active": t.get("active"),
                        }
                        if tab_id:
                            with self._state_lock:
                                self.tab_id_to_info[tab_id] = info
                        collected_tabs.append(info)
                elif isinstance(bs_res, dict) and bs_res.get("isError"):
                    c_list = bs_res.get("content") or []
                    if c_list and isinstance(c_list[0], dict):
                        last_error = c_list[0].get("text") or last_error

            synced_count = self._sync_chrome_tab_rules(collected_tabs)
            return {
                "tabs": collected_tabs,
                "window_count": len(windows),
                "synced_rules_count": synced_count,
                "updated_at": now_str,
                "error": "" if collected_tabs else last_error,
            }

    def _background_tab_sync_loop(self) -> None:
        """背景輕量同步執行緒：啟動後自動同步一次，之後每 15 秒同步新開／關閉的 Chrome 分頁。"""
        time.sleep(2.0)
        while self._running:
            if self._initialized and self._client_inflight == 0:
                try:
                    self._sync_all_chrome_tabs()
                except Exception:
                    pass
            for _ in range(150):  # 15 seconds
                if not self._running:
                    break
                time.sleep(0.1)

    def _background_auto_update_loop(self) -> None:
        """背景無感自動更新執行緒：啟動後延遲 4 秒檢查一次，之後依設定間隔定期檢查 origin/main。"""
        for _ in range(40):  # 4 seconds initial delay so MCP startup stays instant
            if not self._running:
                return
            time.sleep(0.1)

        try:
            interval_hours = max(0.5, float(os.environ.get("CUA_AUTO_UPDATE_INTERVAL_HOURS", "6")))
        except ValueError:
            interval_hours = 6.0
        sleep_ticks = max(600, int(interval_hours * 3600))

        while self._running:
            try:
                res = run_safe_auto_update(self._plugin_root, force=False)
                if res.get("status") == "updated":
                    self._auto_update_result = res
            except Exception:
                pass

            for _ in range(sleep_ticks):
                if not self._running:
                    return
                time.sleep(1.0)

    # -- Virtual MCP App Tools & Resources ----------------------------------
    def _virtual_tools_descriptors(self) -> list[dict]:
        return [
            {
                "name": "open_chrome_tabs_picker",
                "title": "Chrome Tabs",
                "description": (
                    "Open the interactive Chrome Tabs Picker side panel to browse live open Chrome tabs, "
                    "sync @chrome-tab mentions, and attach any tab directly to the chat composer as a context pill."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                "_meta": {
                    "ui": {
                        "resourceUri": CHROME_TABS_UI_URI,
                    },
                    "com.google.antigravity/ui": {
                        "entrypoints": [
                            {"type": "aux_pane", "title": "Chrome Tabs"},
                            {"type": "global", "title": "Chrome Tabs"},
                        ]
                    },
                },
            },
            {
                "name": "refresh_chrome_tabs_app",
                "title": "Refresh Chrome Tabs",
                "description": "App-only helper to refresh live Chrome tabs and @chrome-tab rule files.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                "_meta": {
                    "ui": {
                        "visibility": ["app"],
                    }
                },
            },
        ]

    def _fetch_single_favicon_data_uri(self, scheme: str, netloc: str, hostname: str) -> str:
        """抓取指定網域的網站圖示並轉為 data: URI（無 CSP 阻擋且支援記憶體快取）。"""
        is_local = (
            hostname in ("localhost", "127.0.0.1", "::1")
            or hostname.endswith(".local")
            or bool(re.match(r"^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)", hostname))
        )
        candidates: list[str] = []
        if not is_local and "." in hostname:
            candidates.append(f"https://www.google.com/s2/favicons?domain={urllib.parse.quote(hostname)}&sz=64")
        if scheme in ("http", "https") and netloc:
            candidates.append(f"{scheme}://{netloc}/favicon.ico")

        for icon_url in candidates:
            try:
                req = urllib.request.Request(
                    icon_url,
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                )
                timeout_sec = 0.7 if is_local else 1.4
                with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                    if resp.status != 200:
                        continue
                    raw = resp.read(65537)
                    if not raw or len(raw) > 65536:
                        continue
                    ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                    if not ctype.startswith("image/"):
                        if raw.startswith(b"\x89PNG"):
                            ctype = "image/png"
                        elif raw.startswith(b"\x00\x00\x01\x00"):
                            ctype = "image/x-icon"
                        elif b"<svg" in raw[:256].lower():
                            ctype = "image/svg+xml"
                        else:
                            continue
                    b64 = base64.b64encode(raw).decode("ascii")
                    return f"data:{ctype};base64,{b64}"
            except Exception:
                continue
        return ""

    def _populate_tab_favicons(self, tabs: list[dict]) -> None:
        """批次並行補齊各分頁的網站 favicon data URI（僅於開啟或重整側邊面板時執行）。"""
        if not tabs:
            return
        now_ts = time.time()
        to_fetch: dict[str, tuple[str, str, str]] = {}
        tab_keys: list[str] = []

        for t in tabs:
            url = t.get("url") or ""
            try:
                parsed = urllib.parse.urlparse(url)
                scheme = (parsed.scheme or "").lower()
                netloc = parsed.netloc or ""
                hostname = (parsed.hostname or "").lower()
            except Exception:
                scheme, netloc, hostname = "", "", ""

            if scheme not in ("http", "https") or not hostname:
                tab_keys.append("")
                continue

            cache_key = f"{scheme}://{netloc}" if hostname in ("localhost", "127.0.0.1", "::1") else hostname
            tab_keys.append(cache_key)
            with self._favicon_lock:
                cached = self._favicon_cache.get(cache_key)
                if cached is not None:
                    data_uri, exp_ts = cached
                    if now_ts < exp_ts:
                        continue
            if cache_key not in to_fetch:
                to_fetch[cache_key] = (scheme, netloc, hostname)

        if to_fetch:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(to_fetch))) as pool:
                fut_map = {
                    pool.submit(self._fetch_single_favicon_data_uri, s, nl, hn): key
                    for key, (s, nl, hn) in to_fetch.items()
                }
                done, _ = concurrent.futures.wait(fut_map.keys(), timeout=2.2)
                with self._favicon_lock:
                    if len(self._favicon_cache) > 256:
                        self._favicon_cache.clear()
                    for fut in done:
                        key = fut_map[fut]
                        try:
                            uri = fut.result() or ""
                        except Exception:
                            uri = ""
                        ttl = 3600.0 if uri else 180.0
                        self._favicon_cache[key] = (uri, now_ts + ttl)

        with self._favicon_lock:
            for t, key in zip(tabs, tab_keys):
                if key and key in self._favicon_cache:
                    uri = self._favicon_cache[key][0]
                    if uri:
                        t["favicon"] = uri

    def _handle_virtual_tool_call(self, req_id: object, tool_name: str) -> None:
        payload = self._sync_all_chrome_tabs()
        tabs = payload.get("tabs") or []
        self._populate_tab_favicons(tabs)
        if self._auto_update_result and self._auto_update_result.get("status") == "updated":
            payload["auto_update"] = self._auto_update_result
        if tabs:
            lines = [f"已同步 {len(tabs)} 個開啟中的 Chrome 分頁（皆可於聊天框以 @chrome-tab 選取）："]
            for i, t in enumerate(tabs, start=1):
                mark = " [目前檢視]" if t.get("active") is True else " [背景分頁]"
                rtag = f" (@{t.get('rule_name')})" if t.get("rule_name") else ""
                lines.append(
                    f"{i}. {t.get('title')}{mark}{rtag}\n"
                    f"   url={t.get('url')} | target_id={t.get('target_id')} | tab_id={t.get('tab_id')}"
                )
            text_summary = "\n".join(lines)
        else:
            text_summary = payload.get("error") or "目前沒有偵測到開啟中的 Chrome 分頁。"

        self._send_to_client(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": text_summary}],
                    "structuredContent": payload,
                },
            }
        )

    def _handle_resources_list(self, req_id: object) -> None:
        self._send_to_client(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "resources": [
                        {
                            "uri": CHROME_TABS_UI_URI,
                            "name": "Chrome Tabs Picker",
                            "description": "Interactive Chrome tab picker side panel with one-click Attach to prompt.",
                            "mimeType": "text/html;profile=mcp-app",
                            "_meta": {
                                "ui": {
                                    "prefersBorder": False,
                                    "csp": {
                                        "resourceDomains": [
                                            "https://www.google.com",
                                            "https://*.gstatic.com",
                                        ]
                                    },
                                }
                            },
                        }
                    ]
                },
            }
        )

    def _handle_resources_read(self, req_id: object, uri: str) -> None:
        if uri == CHROME_TABS_UI_URI:
            try:
                with open(self._ui_html_path, "r", encoding="utf-8") as f:
                    html = f.read()
                self._send_to_client(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "contents": [
                                {
                                    "uri": CHROME_TABS_UI_URI,
                                    "mimeType": "text/html;profile=mcp-app",
                                    "text": html,
                                }
                            ]
                        },
                    }
                )
                return
            except Exception as exc:
                self._send_to_client(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32603, "message": f"Failed to read UI HTML: {exc}"},
                    }
                )
                return

        self._send_to_client(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32602, "message": f"Unknown resource URI: {uri}"},
            }
        )

    # -- Resolution & Inspection of Client <-> Upstream Messages ------------
    def _resolve_target_from_args(self, args: dict) -> tuple[int, str]:
        """從工具參數解析 (target_hwnd, expected_tab_title)。"""
        if not isinstance(args, dict):
            return (0, "")

        # 1. 指定了瀏覽器 tab_id -> 啟用 Tab-Aware 分頁感知！
        tab_id = args.get("tab_id")
        if isinstance(tab_id, str) and tab_id in self.tab_id_to_info:
            info = self.tab_id_to_info[tab_id]
            return (int(info.get("window_id") or 0), str(info.get("title") or ""))

        # 2. 指定了瀏覽器 target_id
        btid = args.get("target_id")
        if isinstance(btid, str) and btid in self.browser_target_to_hwnd:
            return (self.browser_target_to_hwnd[btid], "")

        # 3. 直接指定 window_id (一般桌面視窗)
        wid = args.get("window_id")
        if isinstance(wid, int) and wid > 0:
            pid = args.get("pid")
            if isinstance(pid, int) and pid > 0:
                self.pid_to_hwnd[pid] = wid
            return (wid, "")

        # 4. target: { kind: "window", window_id: ... }
        target = args.get("target")
        if isinstance(target, dict):
            twid = target.get("window_id")
            if isinstance(twid, int) and twid > 0:
                tpid = target.get("pid")
                if isinstance(tpid, int) and tpid > 0:
                    self.pid_to_hwnd[tpid] = twid
                return (twid, "")

        # 5. element_token: "s00000005:14"
        token = args.get("element_token")
        if isinstance(token, str) and ":" in token:
            snap_id = token.split(":", 1)[0]
            if snap_id in self.snapshot_to_hwnd:
                return (self.snapshot_to_hwnd[snap_id], "")

        # 6. run_steps
        steps = args.get("steps")
        if isinstance(steps, list):
            for step in steps:
                if isinstance(step, dict):
                    s_args = step.get("args") if isinstance(step.get("args"), dict) else step
                    resolved = self._resolve_target_from_args(s_args)
                    if resolved[0] > 0:
                        return resolved

        # 7. 僅指定 pid
        pid = args.get("pid")
        if isinstance(pid, int) and pid in self.pid_to_hwnd:
            return (self.pid_to_hwnd[pid], "")

        return (0, "")

    @staticmethod
    def _format_browser_structured_summary(tool_name: str, sc: dict) -> str:
        """將 cua-driver 放在 structuredContent 裡的 tabs / outline / refs 轉成精簡文字給模型使用。"""
        if not isinstance(sc, dict):
            return ""

        parts: list[str] = []

        # Bind mode: tabs list
        tabs = sc.get("tabs")
        if isinstance(tabs, list) and tabs:
            parts.append("\n[Tabs]")
            for i, t in enumerate(tabs, start=1):
                if not isinstance(t, dict):
                    continue
                act = " [active]" if t.get("active") is True else (" [background]" if t.get("active") is False else "")
                parts.append(
                    f"  {i}. tab_id={t.get('tab_id')}{act} | title={t.get('title')} | url={t.get('url')}"
                )

        # Snapshot mode: title / url / outline / refs
        if sc.get("title") or sc.get("url"):
            parts.append(f"\n[Page] title={sc.get('title')} | url={sc.get('url')}")
        outline = sc.get("outline")
        if isinstance(outline, str) and outline.strip():
            parts.append("\n[Outline]\n" + outline[:12000])
        elif isinstance(outline, list) and outline:
            parts.append("\n[Outline]\n" + "\n".join(str(x) for x in outline[:200]))

        refs = sc.get("refs")
        if isinstance(refs, list) and refs:
            ref_lines = ["\n[Action Refs]"]
            for r in refs[:180]:
                if isinstance(r, dict):
                    rid = r.get("ref") or r.get("id")
                    role = r.get("role") or ""
                    name = r.get("name") or r.get("label") or r.get("text") or ""
                    acts = r.get("actions")
                    act_str = f" actions={acts}" if acts else ""
                    ref_lines.append(f"  [{rid}] {role} {json.dumps(name, ensure_ascii=False)}{act_str}")
            parts.append("\n".join(ref_lines))

        return "\n".join(parts)

    def _handle_client_line(self, line: bytes) -> None:
        try:
            msg = json.loads(line.decode("utf-8", errors="ignore"))
        except Exception:
            self._send_to_upstream(line)
            return

        if not isinstance(msg, dict):
            self._send_to_upstream(line)
            return

        method = msg.get("method")
        req_id = msg.get("id")

        if method == "notifications/initialized":
            self._initialized = True
            self._send_to_upstream(line)
            return

        # 攔截虛擬 MCP App 資源
        if method == "resources/list" and req_id is not None:
            self._handle_resources_list(req_id)
            return

        if method == "resources/read" and req_id is not None:
            uri = (msg.get("params") or {}).get("uri") or ""
            self._handle_resources_read(req_id, uri)
            return

        # 攔截虛擬工具 (open_chrome_tabs_picker / refresh_chrome_tabs_app)
        if method == "tools/call":
            params = msg.get("params") or {}
            tool_name = params.get("name") or ""
            args = params.get("arguments") or {}
            in_meta = params.get("_meta")
            if isinstance(in_meta, dict) and in_meta:
                with self._state_lock:
                    self._last_client_meta = dict(in_meta)

            if tool_name in ("open_chrome_tabs_picker", "refresh_chrome_tabs_app") and req_id is not None:
                threading.Thread(
                    target=self._handle_virtual_tool_call,
                    args=(req_id, tool_name),
                    daemon=True,
                ).start()
                return

            session_name = args.get("session") if isinstance(args, dict) else None
            with self._state_lock:
                self._client_inflight += 1
                if isinstance(session_name, str) and session_name:
                    self.active_session = session_name
                sess = self.active_session
                hwnd, tab_title = self._resolve_target_from_args(args)
                if req_id is not None:
                    self.req_id_to_meta[req_id] = {
                        "method": method,
                        "tool_name": tool_name,
                        "hwnd": hwnd,
                        "tab_title": tab_title,
                        "tab_id": args.get("tab_id") if isinstance(args, dict) else None,
                        "pid": args.get("pid") if isinstance(args, dict) else None,
                        "session": sess,
                    }

            # 只有對特定分頁或原生視窗操作才點亮外框（bind-only get_browser_state 不先強亮整個 Chrome，待看 tab）
            if hwnd > 0 and not (tool_name == "get_browser_state" and not args.get("tab_id")):
                self.overlay.highlight_window(hwnd, sess, expected_tab_title=tab_title)

        elif req_id is not None and method in ("initialize", "tools/list"):
            with self._state_lock:
                self.req_id_to_meta[req_id] = {"method": method}

        self._send_to_upstream(line)

    def _handle_server_line(self, line: bytes) -> None:
        try:
            msg = json.loads(line.decode("utf-8", errors="ignore"))
        except Exception:
            self._write_raw_to_client(line)
            return

        if not isinstance(msg, dict):
            self._write_raw_to_client(line)
            return

        req_id = msg.get("id")

        # 攔截內部同步 RPC 回應（不轉發給 Antigravity）
        if isinstance(req_id, str) and req_id.startswith("__glow_proxy_"):
            with self._internal_lock:
                pending = self._internal_pending.get(req_id)
            if pending:
                ev, holder = pending
                holder.append(msg)
                ev.set()
            return

        with self._state_lock:
            meta = self.req_id_to_meta.pop(req_id, {}) if req_id is not None else {}
            if meta.get("method") == "tools/call" and self._client_inflight > 0:
                self._client_inflight -= 1

        method = meta.get("method")
        modified = False

        # 1. initialize 回應：宣告支援 resources 能力（MCP App 必需）
        if method == "initialize" and isinstance(msg.get("result"), dict):
            caps = msg["result"].setdefault("capabilities", {})
            if "resources" not in caps:
                caps["resources"] = {"listChanged": False}
                modified = True

        # 2. tools/list 回應：注入 Chrome Tabs Picker 虛擬工具
        elif method == "tools/list" and isinstance(msg.get("result"), dict):
            tools = msg["result"].get("tools")
            if isinstance(tools, list):
                existing_names = {t.get("name") for t in tools if isinstance(t, dict)}
                for vt in self._virtual_tools_descriptors():
                    if vt["name"] not in existing_names:
                        tools.append(vt)
                        modified = True

        # 3. tools/call 回應：追蹤 window_id / snapshot_id / tabs 並橋接 structuredContent
        elif method == "tools/call" and isinstance(msg.get("result"), dict):
            tool_name = meta.get("tool_name") or ""
            sess = meta.get("session") or self.active_session
            req_hwnd = int(meta.get("hwnd") or 0)
            req_tab_title = str(meta.get("tab_title") or "")
            req_tab_id = meta.get("tab_id")
            req_pid = meta.get("pid")
            result = msg["result"]
            sc = result.get("structuredContent") if isinstance(result.get("structuredContent"), dict) else None
            raw_dump = json.dumps(result, ensure_ascii=False)

            detected_hwnd = req_hwnd
            detected_tab_title = req_tab_title
            updated_tabs_for_rules: list[dict] | None = None

            with self._state_lock:
                # 原生視窗 window_id / snapshot_id
                m_win = re.search(r"window_id=(\d+)", raw_dump)
                m_pid = re.search(r"pid=(\d+)", raw_dump)
                m_snap = re.search(r"snapshot_id=(s[0-9a-f]{8})", raw_dump)

                if m_win:
                    detected_hwnd = int(m_win.group(1))
                if detected_hwnd > 0 and m_pid:
                    self.pid_to_hwnd[int(m_pid.group(1))] = detected_hwnd
                if detected_hwnd > 0 and m_snap:
                    self.snapshot_to_hwnd[m_snap.group(1)] = detected_hwnd

                if detected_hwnd == 0:
                    m_bracket = re.search(r"\[window_id:\s*(\d+)\]", raw_dump)
                    if m_bracket:
                        detected_hwnd = int(m_bracket.group(1))

                # 瀏覽器 structuredContent 解析
                if sc is not None:
                    btid = sc.get("target_id")
                    if isinstance(btid, str) and btid:
                        if detected_hwnd > 0:
                            self.browser_target_to_hwnd[btid] = detected_hwnd
                        elif btid in self.browser_target_to_hwnd:
                            detected_hwnd = self.browser_target_to_hwnd[btid]
                        if isinstance(req_pid, int) and req_pid > 0:
                            self.browser_target_to_pid[btid] = req_pid

                    tabs = sc.get("tabs")
                    if isinstance(tabs, list) and tabs:
                        for t in tabs:
                            if isinstance(t, dict) and t.get("tab_id"):
                                info = {
                                    "tab_id": t["tab_id"],
                                    "target_id": btid or "",
                                    "pid": req_pid or self.browser_target_to_pid.get(btid or "", 0),
                                    "window_id": detected_hwnd,
                                    "title": t.get("title") or "",
                                    "url": t.get("url") or "",
                                    "active": t.get("active"),
                                }
                                self.tab_id_to_info[t["tab_id"]] = info
                        updated_tabs_for_rules = list(self.tab_id_to_info.values())

                    # 單一分頁 snapshot 或 browser_navigate 後更新該 tab_id 的最新標題
                    sc_tab_id = sc.get("tab_id") or req_tab_id
                    sc_title = sc.get("title")
                    sc_url = sc.get("url")
                    if isinstance(sc_tab_id, str) and sc_tab_id and isinstance(sc_title, str) and sc_title:
                        detected_tab_title = sc_title
                        if sc_tab_id in self.tab_id_to_info:
                            self.tab_id_to_info[sc_tab_id]["title"] = sc_title
                            if isinstance(sc_url, str) and sc_url:
                                self.tab_id_to_info[sc_tab_id]["url"] = sc_url
                            updated_tabs_for_rules = list(self.tab_id_to_info.values())

            # 將 structuredContent 內的 tabs / outline / refs 補印到 content[0].text 方便模型判讀
            if sc is not None and tool_name in (
                "get_browser_state",
                "browser_navigate",
                "browser_click",
                "browser_type",
                "browser_pointer",
            ):
                extra = self._format_browser_structured_summary(tool_name, sc)
                if extra:
                    c_list = result.get("content")
                    if isinstance(c_list, list) and c_list and isinstance(c_list[0], dict) and c_list[0].get("type") == "text":
                        if "[Tabs]" not in c_list[0].get("text", "") and "[Action Refs]" not in c_list[0].get("text", ""):
                            c_list[0]["text"] = c_list[0].get("text", "") + extra
                            modified = True

            if updated_tabs_for_rules is not None:
                threading.Thread(
                    target=self._sync_chrome_tab_rules,
                    args=(updated_tabs_for_rules,),
                    daemon=True,
                ).start()

            # 點亮目標視窗（瀏覽器分頁操作會帶入 expected_tab_title 啟用分頁感知）
            if detected_hwnd > 0 and not (tool_name == "get_browser_state" and not req_tab_id):
                self.overlay.highlight_window(
                    detected_hwnd,
                    sess,
                    expected_tab_title=detected_tab_title,
                )

        if modified:
            self._send_to_client(msg)
        else:
            self._write_raw_to_client(line)

    def run(self) -> int:
        self._proc = subprocess.Popen(
            [self.cua_binary] + self.cua_args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,
            bufsize=0,
        )
        self._job_handle = bind_process_to_kill_on_close_job(int(self._proc._handle))

        def _forward_stdout() -> None:
            try:
                assert self._proc is not None and self._proc.stdout is not None
                while True:
                    line = self._proc.stdout.readline()
                    if not line:
                        break
                    self._handle_server_line(line)
            except Exception:
                pass

        out_thread = threading.Thread(target=_forward_stdout, daemon=True)
        out_thread.start()

        sync_thread = threading.Thread(target=self._background_tab_sync_loop, daemon=True)
        sync_thread.start()

        update_thread = threading.Thread(target=self._background_auto_update_loop, daemon=True)
        update_thread.start()

        try:
            while True:
                line = sys.stdin.buffer.readline()
                if not line:
                    break
                self._handle_client_line(line)
        except Exception:
            pass
        finally:
            self._running = False
            self.overlay.shutdown()
            try:
                if self._proc and self._proc.stdin:
                    self._proc.stdin.close()
            except Exception:
                pass
            try:
                if self._proc:
                    self._proc.terminate()
            except Exception:
                pass

        return self._proc.wait() if self._proc else 0


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "--check-update":
        plugin_root = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
        force_flag = "--force" in sys.argv[2:]
        res = run_safe_auto_update(plugin_root, force=force_flag)
        print(json.dumps(res, ensure_ascii=False))
        return

    if len(sys.argv) >= 3 and sys.argv[1] == "--test-hwnd":
        hwnd = int(sys.argv[2])
        sess = sys.argv[3] if len(sys.argv) >= 4 else "agy-cursor"
        exp_tab = sys.argv[4] if len(sys.argv) >= 5 else ""
        ov = WindowGlowOverlay()
        time.sleep(0.1)
        ov.highlight_window(hwnd, sess, expected_tab_title=exp_tab)
        time.sleep(4.5)
        ov.shutdown()
        return

    cua_bin = discover_cua_driver_binary()
    cua_args = sys.argv[1:] if len(sys.argv) > 1 else ["mcp", "--grant", "existing-profile"]
    proxy = CuaMcpGlowProxy(cua_bin, cua_args)
    sys.exit(proxy.run())


if __name__ == "__main__":
    main()
