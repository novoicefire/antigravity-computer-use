# -*- coding: utf-8 -*-
# 檔案用途：本機 Chrome CDP 救援通道（支援多視窗 heuristic 唯讀突破、遠端桌面 <canvas> 背景座標點擊／拖曳／鍵盤輸入與同色虛擬游標回饋）
# 架構原則：純 Python 標準庫實作 RFC 6455 Localhost WebSocket 與 Win32 分頁感知虛擬游標圖層，零外部套件依賴、不搶實體滑鼠與焦點。

from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import json
import math
import os
import socket
import struct
import threading
import time
from typing import Callable

# ---------------------------------------------------------------------------
# Win32 Constants & Signatures for ProxyVirtualCursorOverlay
# ---------------------------------------------------------------------------
WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

GWL_EXSTYLE = -20
GW_HWNDPREV = 3

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_NOOWNERZORDER = 0x0200

HWND_TOP = wintypes.HWND(0)
HWND_NOTOPMOST = wintypes.HWND(-2 & 0xFFFFFFFFFFFFFFFF)

ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
DIB_RGB_COLORS = 0
DWMWA_EXTENDED_FRAME_BOUNDS = 9
WDA_EXCLUDEFROMCAPTURE = 0x00000011

WM_DESTROY = 0x0002
WM_TIMER = 0x0113

BASE_CURSOR_BOX_SIZE = 48
CURSOR_HOLD_SEC = 1.5
CURSOR_FADE_SEC = 0.35
EXCLUDE_OVERLAY_FROM_CAPTURE = os.environ.get("CUA_EXCLUDE_FROM_CAPTURE", "false").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)


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


if os.name == "nt":
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    kernel32 = ctypes.windll.kernel32
    dwmapi = ctypes.windll.dwmapi

    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = ctypes.c_ssize_t
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
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


# ---------------------------------------------------------------------------
# Tab-Aware Z-Order Virtual Cursor Overlay (for CDP Rescue & chrome-devtools)
# ---------------------------------------------------------------------------
class ProxyVirtualCursorOverlay:
    """當使用 CDP 救援或 chrome-devtools 時，在目標視窗同圖層繪製分頁感知的同色虛擬游標與點擊漣漪。"""

    def __init__(self, title_matcher: Callable[[str, str], int]) -> None:
        self._title_matcher = title_matcher
        self._lock = threading.Lock()
        self._target_hwnd: int = 0
        self._expected_tab_title: str = ""
        self._screen_x: int = 0
        self._screen_y: int = 0
        self._from_x: int = 0
        self._from_y: int = 0
        self._last_ts: float = 0.0
        self._dpi_scale: float = 1.0
        self._color_rgb: tuple[int, int, int] = (0x4C, 0xCC, 0xE0)
        self._cached_key: tuple[tuple[int, int, int], int] = ((0, 0, 0), 0)
        self._box_size: int = BASE_CURSOR_BOX_SIZE
        self._hotspot_offset: int = 12
        self._cursor_hwnd: int = 0
        self._dib_hdc = None
        self._dib_bmp = None
        self._dib_old = None
        self._dib_bits = ctypes.c_void_p()
        self._running = os.name == "nt"
        if self._running:
            self._thread = threading.Thread(target=self._ui_thread, daemon=True)
            self._thread.start()

    def pulse_at_viewport(
        self,
        hwnd: int,
        rgb: tuple[int, int, int],
        viewport_x: float | None = None,
        viewport_y: float | None = None,
        expected_tab_title: str = "",
        is_browser: bool = True,
    ) -> None:
        if not self._running or not hwnd or hwnd <= 0:
            return
        clean_expected = expected_tab_title.strip()
        if is_browser and not clean_expected:
            return
        if not user32.IsWindow(wintypes.HWND(hwnd)):
            return
        bounds = self._get_window_bounds(hwnd)
        if not bounds:
            return
        wx, wy, ww, wh = bounds
        dpi_scale = 1.0
        try:
            dpi = user32.GetDpiForWindow(wintypes.HWND(hwnd))
            if dpi and dpi > 0:
                dpi_scale = max(1.0, min(3.0, float(dpi) / 96.0))
        except Exception:
            pass

        chrome_top_px = int(86 * dpi_scale)
        if viewport_x is not None and viewport_y is not None:
            sx = wx + int(float(viewport_x) * dpi_scale)
            sy = wy + chrome_top_px + int(float(viewport_y) * dpi_scale)
        else:
            # 無明確座標的工具（例如 DOM uid 點擊或鍵盤輸入）顯示於視窗內容區右上方溫和指示點
            sx = wx + ww - int(72 * dpi_scale)
            sy = wy + chrome_top_px + int(36 * dpi_scale)

        sx = max(wx + 12, min(wx + ww - 12, sx))
        sy = max(wy + 12, min(wy + wh - 12, sy))

        with self._lock:
            now = time.monotonic()
            if self._target_hwnd == hwnd and (now - self._last_ts) < 3.0 and self._screen_x > 0:
                self._from_x = self._screen_x
                self._from_y = self._screen_y
            else:
                self._from_x = max(wx + 20, sx - int(36 * dpi_scale))
                self._from_y = max(wy + 20, sy - int(28 * dpi_scale))
            self._target_hwnd = hwnd
            self._expected_tab_title = clean_expected
            self._screen_x = sx
            self._screen_y = sy
            self._dpi_scale = dpi_scale
            self._color_rgb = rgb
            self._last_ts = now

    def shutdown(self) -> None:
        self._running = False

    @staticmethod
    def _get_window_bounds(hwnd: int) -> tuple[int, int, int, int] | None:
        rect = RECT()
        hr = dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd),
            DWMWA_EXTENDED_FRAME_BOUNDS,
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
        if hr != 0 and not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            return None
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w <= 20 or h <= 20:
            return None
        return (rect.left, rect.top, w, h)

    def _is_tab_visible(self, hwnd: int, exp_title: str) -> bool:
        if not exp_title:
            return True
        buf = ctypes.create_unicode_buffer(512)
        n = user32.GetWindowTextW(wintypes.HWND(hwnd), buf, 512)
        live = buf.value if n > 0 else ""
        return self._title_matcher(live, exp_title) > 0

    def _ensure_cursor_dib(self, rgb: tuple[int, int, int], dpi_scale: float) -> None:
        size = max(BASE_CURSOR_BOX_SIZE, int(round(BASE_CURSOR_BOX_SIZE * dpi_scale)))
        key = (rgb, size)
        if self._cached_key == key and self._dib_hdc and self._dib_bits:
            return
        self._free_dib()
        self._box_size = size
        self._hotspot_offset = int(round(12.0 * dpi_scale))
        screen_dc = user32.GetDC(None)
        self._dib_hdc = gdi32.CreateCompatibleDC(screen_dc)
        user32.ReleaseDC(None, screen_dc)

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = size
        bmi.bmiHeader.biHeight = -size
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = DIB_RGB_COLORS

        bits = ctypes.c_void_p()
        self._dib_bmp = gdi32.CreateDIBSection(
            self._dib_hdc, ctypes.byref(bmi), DIB_RGB_COLORS, ctypes.byref(bits), None, 0
        )
        self._dib_old = gdi32.SelectObject(self._dib_hdc, self._dib_bmp)
        self._dib_bits = bits
        self._cached_key = key

        # 繪製 DPI 自適應的高對比同色游標箭頭 + 柔和霓虹光暈圓環
        raw = bytearray(size * size * 4)
        nr, ng, nb = rgb
        tip_x = float(self._hotspot_offset)
        tip_y = float(self._hotspot_offset)

        def _put_px(px: int, py: int, r: int, g: int, b: int, a: int) -> None:
            if 0 <= px < size and 0 <= py < size and a > 0:
                idx = (py * size + px) * 4
                pr = (r * a) // 255
                pg = (g * a) // 255
                pb = (b * a) // 255
                old_a = raw[idx + 3]
                if old_a == 0:
                    raw[idx : idx + 4] = struct.pack("<BBBB", pb, pg, pr, a)
                else:
                    inv = 255 - a
                    out_a = a + (old_a * inv) // 255
                    out_b = pb + (raw[idx] * inv) // 255
                    out_g = pg + (raw[idx + 1] * inv) // 255
                    out_r = pr + (raw[idx + 2] * inv) // 255
                    raw[idx : idx + 4] = struct.pack("<BBBB", min(255, out_b), min(255, out_g), min(255, out_r), min(255, out_a))

        # 1. 點擊熱點柔和同色光圈（在邏輯座標空間計算，確保 200% DPI 清晰飽滿）
        for y in range(size):
            for x in range(size):
                dist = math.hypot((x - tip_x) / dpi_scale, (y - tip_y) / dpi_scale)
                if 5.0 <= dist <= 10.5:
                    ring_a = int(155 * max(0.0, 1.0 - abs(dist - 7.6) / 2.8))
                    _put_px(x, y, nr, ng, nb, ring_a)

        # 2. 向量箭頭多邊形 (白邊 + 同色內芯)
        y_min = max(0, int((tip_y - 2.0 * dpi_scale)))
        y_max = min(size, int(tip_y + 26.0 * dpi_scale))
        x_min = max(0, int((tip_x - 2.0 * dpi_scale)))
        x_max = min(size, int(tip_x + 22.0 * dpi_scale))
        for y in range(y_min, y_max):
            for x in range(x_min, x_max):
                rx = (x - tip_x) / dpi_scale
                ry = (y - tip_y) / dpi_scale
                if rx >= -1.0 and ry >= -1.0 and rx <= ry * 0.76 + 1.2 and (rx + ry * 0.55) <= 14.8:
                    inside_core = (
                        rx >= 0.45
                        and ry >= 0.85
                        and rx <= ry * 0.72 - 0.25
                        and (rx + ry * 0.55) <= 13.2
                    )
                    if inside_core:
                        _put_px(x, y, nr, ng, nb, 252)
                    else:
                        _put_px(x, y, 255, 255, 255, 240)

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
        self._cached_key = ((0, 0, 0), 0)

    def _pin_above_target(self, target_hwnd: int) -> None:
        if not self._cursor_hwnd or not user32.IsWindow(wintypes.HWND(target_hwnd)):
            return
        prev_hwnd = user32.GetWindow(wintypes.HWND(target_hwnd), GW_HWNDPREV)
        while prev_hwnd and int(prev_hwnd) == self._cursor_hwnd:
            prev_hwnd = user32.GetWindow(wintypes.HWND(prev_hwnd), GW_HWNDPREV)

        target_ex = user32.GetWindowLongW(wintypes.HWND(target_hwnd), GWL_EXSTYLE)
        target_topmost = bool(target_ex & WS_EX_TOPMOST)
        insert_after = HWND_TOP
        if prev_hwnd:
            prev_ex = user32.GetWindowLongW(wintypes.HWND(prev_hwnd), GWL_EXSTYLE)
            if target_topmost == bool(prev_ex & WS_EX_TOPMOST):
                insert_after = wintypes.HWND(int(prev_hwnd))

        if not target_topmost:
            cur_ex = user32.GetWindowLongW(wintypes.HWND(self._cursor_hwnd), GWL_EXSTYLE)
            if cur_ex & WS_EX_TOPMOST:
                user32.SetWindowPos(
                    wintypes.HWND(self._cursor_hwnd),
                    HWND_NOTOPMOST,
                    0, 0, 0, 0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER,
                )

        user32.SetWindowPos(
            wintypes.HWND(self._cursor_hwnd),
            insert_after,
            0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER,
        )

    def _tick(self) -> None:
        with self._lock:
            target = self._target_hwnd
            exp_title = self._expected_tab_title
            tx, ty = self._screen_x, self._screen_y
            fx, fy = self._from_x, self._from_y
            last_ts = self._last_ts
            dpi_scale = self._dpi_scale
            rgb = self._color_rgb

        if not target or not self._cursor_hwnd:
            return

        elapsed = time.monotonic() - last_ts
        if (
            elapsed > (CURSOR_HOLD_SEC + CURSOR_FADE_SEC)
            or not user32.IsWindow(wintypes.HWND(target))
            or not user32.IsWindowVisible(wintypes.HWND(target))
            or user32.IsIconic(wintypes.HWND(target))
            or not self._is_tab_visible(target, exp_title)
        ):
            if user32.IsWindowVisible(wintypes.HWND(self._cursor_hwnd)):
                user32.ShowWindow(wintypes.HWND(self._cursor_hwnd), 0)
            return

        self._ensure_cursor_dib(rgb, dpi_scale)

        # 0.16s ease-out 平滑移動過渡
        t_move = min(1.0, elapsed / 0.16)
        ease = 1.0 - (1.0 - t_move) ** 3
        cx = int(fx + (tx - fx) * ease) - self._hotspot_offset
        cy = int(fy + (ty - fy) * ease) - self._hotspot_offset

        if elapsed <= CURSOR_HOLD_SEC:
            alpha = 255
        else:
            alpha = max(0, min(255, int(255 * (1.0 - (elapsed - CURSOR_HOLD_SEC) / CURSOR_FADE_SEC))))

        pt_dst = POINT(cx, cy)
        sz = SIZE(self._box_size, self._box_size)
        pt_src = POINT(0, 0)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, alpha, AC_SRC_ALPHA)

        screen_dc = user32.GetDC(None)
        user32.UpdateLayeredWindow(
            wintypes.HWND(self._cursor_hwnd),
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

        if not user32.IsWindowVisible(wintypes.HWND(self._cursor_hwnd)):
            user32.ShowWindow(wintypes.HWND(self._cursor_hwnd), 4)
        self._pin_above_target(target)

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
        cls_name = "Cua.ProxyCursorOverlay"
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wnd_proc_ref
        wc.hInstance = hinst
        wc.lpszClassName = cls_name
        user32.RegisterClassExW(ctypes.byref(wc))

        ex_style = WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        hwnd = user32.CreateWindowExW(
            ex_style,
            cls_name,
            cls_name,
            WS_POPUP,
            0, 0, BASE_CURSOR_BOX_SIZE, BASE_CURSOR_BOX_SIZE,
            None, None, hinst, None,
        )
        self._cursor_hwnd = int(hwnd) if hwnd else 0
        if self._cursor_hwnd:
            if EXCLUDE_OVERLAY_FROM_CAPTURE:
                try:
                    user32.SetWindowDisplayAffinity(wintypes.HWND(self._cursor_hwnd), WDA_EXCLUDEFROMCAPTURE)
                except Exception:
                    pass
            user32.SetTimer(wintypes.HWND(self._cursor_hwnd), 1, 16, None)

        msg = wintypes.MSG()
        while self._running and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        self._free_dib()


# ---------------------------------------------------------------------------
# Minimal Pure-Stdlib RFC 6455 Localhost WebSocket CDP Client
# ---------------------------------------------------------------------------
def discover_devtools_endpoint() -> tuple[str, int, str] | None:
    """讀取本機已啟用遠端偵錯之 Chrome DevToolsActivePort，回傳 (host, port, ws_path)。"""
    local_app = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        os.path.join(local_app, "Google", "Chrome", "User Data", "DevToolsActivePort"),
        os.path.join(local_app, "Google", "Chrome Beta", "User Data", "DevToolsActivePort"),
        os.path.join(local_app, "Microsoft", "Edge", "User Data", "DevToolsActivePort"),
    ]
    for path in candidates:
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                lines = [ln.strip() for ln in f.read().splitlines() if ln.strip()]
            if len(lines) >= 2:
                port = int(lines[0])
                ws_path = lines[1]
                if 1 <= port <= 65535 and ws_path.startswith("/"):
                    return ("127.0.0.1", port, ws_path)
        except Exception:
            continue
    return None


class MinimalCdpClient:
    """純標準庫實作之本機 Loopback CDP WebSocket 客戶端。"""

    def __init__(self, host: str, port: int, path: str, timeout: float = 5.0) -> None:
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(timeout)
        self._seq = 0
        self._buf = bytearray()
        self._handshake(host, port, path)

    def _handshake(self, host: str, port: int, path: str) -> None:
        ws_key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {ws_key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii")
        self._sock.sendall(req)

        header_buf = bytearray()
        while b"\r\n\r\n" not in header_buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("CDP WebSocket handshake closed unexpectedly")
            header_buf.extend(chunk)
            if len(header_buf) > 32768:
                raise ConnectionError("CDP WebSocket handshake header overflow")

        sep_idx = header_buf.index(b"\r\n\r\n")
        head_text = header_buf[:sep_idx].decode("latin1", errors="ignore")
        if "101" not in head_text.splitlines()[0]:
            raise ConnectionError(f"CDP WebSocket upgrade rejected: {head_text.splitlines()[0]}")
        # 保留 HTTP 回應之後多讀到的 WebSocket frame bytes
        self._buf.extend(header_buf[sep_idx + 4 :])

    def _read_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise ConnectionError("CDP WebSocket EOF")
            self._buf.extend(chunk)
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        mask_key = os.urandom(4)
        length = len(payload)
        header = bytearray([0x80 | (opcode & 0x0F)])
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        header.extend(mask_key)

        masked = bytearray(payload)
        for i in range(length):
            masked[i] ^= mask_key[i & 3]
        self._sock.sendall(header + masked)

    def _recv_message(self) -> str:
        fragments: list[bytes] = []
        while True:
            b0, b1 = self._read_exact(2)
            fin = bool(b0 & 0x80)
            opcode = b0 & 0x0F
            masked = bool(b1 & 0x80)
            length = b1 & 0x7F
            if length == 126:
                (length,) = struct.unpack("!H", self._read_exact(2))
            elif length == 127:
                (length,) = struct.unpack("!Q", self._read_exact(8))
            mask_key = self._read_exact(4) if masked else b""
            data = bytearray(self._read_exact(length))
            if masked:
                for i in range(length):
                    data[i] ^= mask_key[i & 3]

            if opcode == 0x8:  # Close
                raise ConnectionError("CDP WebSocket closed by browser")
            if opcode == 0x9:  # Ping -> Pong
                self._send_frame(0xA, bytes(data))
                continue
            if opcode == 0xA:  # Pong
                continue
            if opcode in (0x1, 0x2, 0x0):
                fragments.append(bytes(data))
                if fin:
                    return b"".join(fragments).decode("utf-8", errors="replace")

    def call(
        self,
        method: str,
        params: dict | None = None,
        session_id: str | None = None,
        timeout: float = 8.0,
    ) -> dict:
        self._sock.settimeout(timeout)
        self._seq += 1
        req_id = self._seq
        msg: dict = {"id": req_id, "method": method, "params": params or {}}
        if session_id:
            msg["sessionId"] = session_id
        self._send_frame(0x1, json.dumps(msg, ensure_ascii=False).encode("utf-8"))

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            raw = self._recv_message()
            obj = json.loads(raw)
            if isinstance(obj, dict) and obj.get("id") == req_id:
                if "error" in obj:
                    err = obj["error"]
                    raise RuntimeError(f"CDP {method} error: {err}")
                return obj.get("result") or {}
        raise TimeoutError(f"CDP {method} timed out")

    def close(self) -> None:
        try:
            self._send_frame(0x8, b"")
        except Exception:
            pass
        try:
            self._sock.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Key Mapping & CDP Rescue Manager
# ---------------------------------------------------------------------------
SPECIAL_KEYS: dict[str, tuple[str, str, int]] = {
    "enter": ("Enter", "Enter", 13),
    "return": ("Enter", "Enter", 13),
    "tab": ("Tab", "Tab", 9),
    "escape": ("Escape", "Escape", 27),
    "esc": ("Escape", "Escape", 27),
    "backspace": ("Backspace", "Backspace", 8),
    "delete": ("Delete", "Delete", 46),
    "space": (" ", "Space", 32),
    "arrowup": ("ArrowUp", "ArrowUp", 38),
    "up": ("ArrowUp", "ArrowUp", 38),
    "arrowdown": ("ArrowDown", "ArrowDown", 40),
    "down": ("ArrowDown", "ArrowDown", 40),
    "arrowleft": ("ArrowLeft", "ArrowLeft", 37),
    "left": ("ArrowLeft", "ArrowLeft", 37),
    "arrowright": ("ArrowRight", "ArrowRight", 39),
    "right": ("ArrowRight", "ArrowRight", 39),
    "home": ("Home", "Home", 36),
    "end": ("End", "End", 35),
    "pageup": ("PageUp", "PageUp", 33),
    "pagedown": ("PageDown", "PageDown", 34),
}

SNAPSHOT_JS = r"""
(() => {
  const vw = window.innerWidth || document.documentElement.clientWidth || 1280;
  const vh = window.innerHeight || document.documentElement.clientHeight || 720;
  const refs = [];
  const outline = [];
  const candidates = document.querySelectorAll(
    'canvas, video, a[href], button, input, textarea, select, [role="button"], [role="link"], [role="tab"], [role="menuitem"], [contenteditable="true"], summary'
  );
  let idx = 0;
  for (const el of candidates) {
    if (idx >= 180) break;
    const rect = el.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) continue;
    if (rect.bottom < 0 || rect.right < 0 || rect.top > vh * 1.5) continue;
    const tag = el.tagName.toLowerCase();
    const role = el.getAttribute('role') || (tag === 'a' ? 'link' : tag);
    const rawName = (
      el.getAttribute('aria-label') ||
      el.innerText ||
      el.value ||
      el.getAttribute('placeholder') ||
      el.getAttribute('title') ||
      (tag === 'canvas' ? `canvas (${Math.round(rect.width)}x${Math.round(rect.height)})` : '')
    ) || '';
    const name = String(rawName).replace(/\s+/g, ' ').trim().slice(0, 100);
    const refId = `p1:${idx}`;
    el.setAttribute('data-cua-rescue-ref', refId);
    const cx = Math.round(rect.left + rect.width / 2);
    const cy = Math.round(rect.top + rect.height / 2);
    const actions = tag === 'input' || tag === 'textarea' || el.isContentEditable
      ? ['click', 'type', 'pointer']
      : ['click', 'pointer'];
    refs.push({
      ref: refId,
      role,
      name,
      x: cx,
      y: cy,
      bounds: { x: Math.round(rect.left), y: Math.round(rect.top), width: Math.round(rect.width), height: Math.round(rect.height) },
      actions
    });
    outline.push(`- ${role} ${JSON.stringify(name)} [${refId}] @(${cx},${cy} ${Math.round(rect.width)}x${Math.round(rect.height)})`);
    idx++;
  }
  const headings = document.querySelectorAll('h1, h2, h3');
  const hLines = [];
  for (const h of headings) {
    const t = (h.innerText || '').replace(/\s+/g, ' ').trim();
    if (t) hLines.push(`- heading ${JSON.stringify(t.slice(0, 120))}`);
    if (hLines.length >= 25) break;
  }
  return {
    title: document.title || '',
    url: location.href || '',
    viewport: { width: vw, height: vh },
    outline: [...hLines, ...outline].join('\n'),
    refs
  };
})()
"""


class CdpHeuristicRescueManager:
    """當 cua-driver 因多視窗或遠端桌面輔助視窗進入 heuristic 唯讀時，透過本機 CDP 無縫接手背景操控。"""

    def __init__(self, title_matcher: Callable[[str, str], int]) -> None:
        self._title_matcher = title_matcher
        self._lock = threading.Lock()
        self._tab_refs: dict[str, dict[str, dict]] = {}

    def _connect_and_attach_tab(self, tab_info: dict) -> tuple[MinimalCdpClient, str, dict]:
        ep = discover_devtools_endpoint()
        if not ep:
            raise RuntimeError(
                "DevToolsActivePort not found; enable remote debugging at chrome://inspect/#remote-debugging"
            )
        client = MinimalCdpClient(ep[0], ep[1], ep[2], timeout=6.0)
        try:
            targets = client.call("Target.getTargets").get("targetInfos") or []
            pages = [t for t in targets if isinstance(t, dict) and t.get("type") == "page"]
            if not pages:
                raise RuntimeError("No open Chrome page targets found in CDP")

            want_url = (tab_info.get("url") or "").strip()
            want_title = (tab_info.get("title") or "").strip()

            chosen = None
            # 1. URL + 標題雙重吻合
            for p in pages:
                if want_url and p.get("url") == want_url and want_title and p.get("title") == want_title:
                    chosen = p
                    break
            # 2. URL 完全吻合
            if not chosen and want_url:
                for p in pages:
                    if p.get("url") == want_url:
                        chosen = p
                        break
                if not chosen:
                    base_want = want_url.split("#", 1)[0]
                    for p in pages:
                        if (p.get("url") or "").split("#", 1)[0] == base_want:
                            chosen = p
                            break
            # 3. 正規化標題吻合
            if not chosen and want_title:
                best_score = 0
                for p in pages:
                    sc = self._title_matcher(p.get("title") or "", want_title)
                    if sc > best_score:
                        best_score = sc
                        chosen = p

            if not chosen:
                raise RuntimeError(f"Could not uniquely match tab ({want_title!r} / {want_url!r}) in CDP")

            sid = client.call(
                "Target.attachToTarget",
                {"targetId": chosen["targetId"], "flatten": True},
            ).get("sessionId")
            if not sid:
                raise RuntimeError("CDP attachToTarget returned empty sessionId")
            return client, sid, chosen
        except Exception:
            client.close()
            raise

    @staticmethod
    def _dispatch_mouse_click(
        client: MinimalCdpClient,
        sid: str,
        x: float,
        y: float,
        button: str = "left",
        click_count: int = 1,
    ) -> None:
        btn_mask = 1 if button == "left" else (2 if button == "right" else 4)
        client.call(
            "Input.dispatchMouseEvent",
            {"type": "mouseMoved", "x": float(x), "y": float(y), "button": "none", "buttons": 0},
            session_id=sid,
        )
        for c in range(1, click_count + 1):
            client.call(
                "Input.dispatchMouseEvent",
                {
                    "type": "mousePressed",
                    "x": float(x),
                    "y": float(y),
                    "button": button,
                    "buttons": btn_mask,
                    "clickCount": c,
                },
                session_id=sid,
            )
            client.call(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseReleased",
                    "x": float(x),
                    "y": float(y),
                    "button": button,
                    "buttons": 0,
                    "clickCount": c,
                },
                session_id=sid,
            )

    def rescue_tool(
        self,
        tool_name: str,
        args: dict,
        tab_info: dict,
    ) -> tuple[dict, tuple[float | None, float | None], dict]:
        """
        執行 CDP 救援，回傳 (mcp_result_dict, (cursor_vx, cursor_vy), updated_tab_meta)。
        """
        tab_key = str(args.get("tab_id") or tab_info.get("tab_id") or "")
        client, sid, target_meta = self._connect_and_attach_tab(tab_info)
        cursor_pt: tuple[float | None, float | None] = (None, None)
        updated_meta: dict = {
            "title": target_meta.get("title") or tab_info.get("title") or "",
            "url": target_meta.get("url") or tab_info.get("url") or "",
        }

        try:
            if tool_name == "get_browser_state":
                eval_res = client.call(
                    "Runtime.evaluate",
                    {"expression": SNAPSHOT_JS, "returnByValue": True},
                    session_id=sid,
                )
                snap = ((eval_res.get("result") or {}).get("value")) or {}
                refs = snap.get("refs") or []
                with self._lock:
                    self._tab_refs[tab_key] = {r["ref"]: r for r in refs if isinstance(r, dict) and r.get("ref")}

                updated_meta["title"] = snap.get("title") or updated_meta["title"]
                updated_meta["url"] = snap.get("url") or updated_meta["url"]
                vp = snap.get("viewport") or {}

                text_lines = [
                    f"semantic snapshot (CDP rescue) of {updated_meta['url']} "
                    f"[viewport {vp.get('width')}x{vp.get('height')}]: {len(refs)} action ref(s)",
                    f"\n[Page] title={updated_meta['title']} | url={updated_meta['url']}",
                ]
                if snap.get("outline"):
                    text_lines.append("\n[Outline]\n" + str(snap["outline"])[:10000])
                if refs:
                    text_lines.append("\n[Action Refs]")
                    for r in refs[:150]:
                        text_lines.append(
                            f"  [{r['ref']}] {r.get('role', '')} {json.dumps(r.get('name', ''), ensure_ascii=False)} "
                            f"@({r.get('x')},{r.get('y')}) actions={r.get('actions')}"
                        )

                content_items: list[dict] = [{"type": "text", "text": "\n".join(text_lines)}]
                if args.get("include_screenshot"):
                    shot = client.call("Page.captureScreenshot", {"format": "png"}, session_id=sid)
                    b64_png = shot.get("data") or ""
                    if b64_png:
                        content_items.append(
                            {"type": "image", "data": b64_png, "mimeType": "image/png"}
                        )

                sc = {
                    "status": "ok",
                    "tab_id": tab_key,
                    "target_id": args.get("target_id") or tab_info.get("target_id") or "",
                    "title": updated_meta["title"],
                    "url": updated_meta["url"],
                    "viewport": vp,
                    "refs": refs,
                }
                return ({"content": content_items, "structuredContent": sc}, cursor_pt, updated_meta)

            if tool_name == "browser_navigate":
                dest_url = str(args.get("url") or "")
                if not dest_url:
                    raise ValueError("browser_navigate requires url")
                client.call("Page.navigate", {"url": dest_url}, session_id=sid)
                time.sleep(0.45)
                meta_eval = client.call(
                    "Runtime.evaluate",
                    {"expression": "({title: document.title || '', url: location.href || ''})", "returnByValue": True},
                    session_id=sid,
                )
                mv = ((meta_eval.get("result") or {}).get("value")) or {}
                updated_meta["title"] = mv.get("title") or updated_meta["title"]
                updated_meta["url"] = mv.get("url") or dest_url
                sc = {"status": "ok", "tab_id": tab_key, "title": updated_meta["title"], "url": updated_meta["url"]}
                return (
                    {
                        "content": [{"type": "text", "text": f"navigated {tab_key} to {updated_meta['url']} (CDP rescue)"}],
                        "structuredContent": sc,
                    },
                    cursor_pt,
                    updated_meta,
                )

            if tool_name == "browser_click":
                ref = args.get("ref")
                vx = args.get("x")
                vy = args.get("y")
                if (vx is None or vy is None) and isinstance(ref, str) and ref:
                    with self._lock:
                        rinfo = (self._tab_refs.get(tab_key) or {}).get(ref)
                    if rinfo:
                        vx, vy = rinfo.get("x"), rinfo.get("y")
                    if args.get("input_route") == "dom_event" or vx is None or vy is None:
                        js_click = (
                            f"(() => {{ const el = document.querySelector('[data-cua-rescue-ref={json.dumps(ref)}]'); "
                            "if (!el) return null; const r = el.getBoundingClientRect(); el.click(); "
                            "return {x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2), title: document.title, url: location.href}; })()"
                        )
                        cres = client.call("Runtime.evaluate", {"expression": js_click, "returnByValue": True}, session_id=sid)
                        cval = ((cres.get("result") or {}).get("value")) or {}
                        if not cval:
                            raise RuntimeError(f"Ref {ref} not found on page; refresh snapshot with get_browser_state")
                        cursor_pt = (cval.get("x"), cval.get("y"))
                        updated_meta["title"] = cval.get("title") or updated_meta["title"]
                        updated_meta["url"] = cval.get("url") or updated_meta["url"]
                        return (
                            {
                                "content": [{"type": "text", "text": f"clicked {ref} via DOM event (CDP rescue)"}],
                                "structuredContent": {"status": "ok", "tab_id": tab_key, **updated_meta},
                            },
                            cursor_pt,
                            updated_meta,
                        )

                if vx is None or vy is None:
                    raise ValueError("browser_click requires x/y coordinates or a valid ref")
                self._dispatch_mouse_click(client, sid, float(vx), float(vy), button="left", click_count=1)
                cursor_pt = (float(vx), float(vy))
                time.sleep(0.15)
                mres = client.call(
                    "Runtime.evaluate",
                    {"expression": "({title: document.title || '', url: location.href || ''})", "returnByValue": True},
                    session_id=sid,
                )
                mv = ((mres.get("result") or {}).get("value")) or {}
                updated_meta["title"] = mv.get("title") or updated_meta["title"]
                updated_meta["url"] = mv.get("url") or updated_meta["url"]
                return (
                    {
                        "content": [{"type": "text", "text": f"clicked ({vx}, {vy}) in {tab_key} (CDP rescue)"}],
                        "structuredContent": {"status": "ok", "tab_id": tab_key, **updated_meta},
                    },
                    cursor_pt,
                    updated_meta,
                )

            if tool_name == "browser_pointer":
                action = str(args.get("action") or args.get("kind") or "click").lower()
                vx = float(args.get("x", 0))
                vy = float(args.get("y", 0))
                btn = str(args.get("button") or "left").lower()
                btn_mask = 1 if btn == "left" else (2 if btn == "right" else 4)
                cursor_pt = (vx, vy)

                if action == "move":
                    client.call(
                        "Input.dispatchMouseEvent",
                        {"type": "mouseMoved", "x": vx, "y": vy, "button": "none", "buttons": 0},
                        session_id=sid,
                    )
                elif action in ("click", "single_click"):
                    self._dispatch_mouse_click(client, sid, vx, vy, button=btn, click_count=1)
                elif action == "double_click":
                    self._dispatch_mouse_click(client, sid, vx, vy, button=btn, click_count=2)
                elif action == "right_click":
                    self._dispatch_mouse_click(client, sid, vx, vy, button="right", click_count=1)
                elif action == "down":
                    client.call(
                        "Input.dispatchMouseEvent",
                        {"type": "mousePressed", "x": vx, "y": vy, "button": btn, "buttons": btn_mask, "clickCount": 1},
                        session_id=sid,
                    )
                elif action == "up":
                    client.call(
                        "Input.dispatchMouseEvent",
                        {"type": "mouseReleased", "x": vx, "y": vy, "button": btn, "buttons": 0, "clickCount": 1},
                        session_id=sid,
                    )
                elif action == "drag":
                    tx = float(args.get("to_x", args.get("end_x", vx)))
                    ty = float(args.get("to_y", args.get("end_y", vy)))
                    client.call(
                        "Input.dispatchMouseEvent",
                        {"type": "mouseMoved", "x": vx, "y": vy, "button": "none", "buttons": 0},
                        session_id=sid,
                    )
                    client.call(
                        "Input.dispatchMouseEvent",
                        {"type": "mousePressed", "x": vx, "y": vy, "button": btn, "buttons": btn_mask, "clickCount": 1},
                        session_id=sid,
                    )
                    for step_i in range(1, 7):
                        ix = vx + (tx - vx) * (step_i / 6.0)
                        iy = vy + (ty - vy) * (step_i / 6.0)
                        client.call(
                            "Input.dispatchMouseEvent",
                            {"type": "mouseMoved", "x": ix, "y": iy, "button": btn, "buttons": btn_mask},
                            session_id=sid,
                        )
                        time.sleep(0.015)
                    client.call(
                        "Input.dispatchMouseEvent",
                        {"type": "mouseReleased", "x": tx, "y": ty, "button": btn, "buttons": 0, "clickCount": 1},
                        session_id=sid,
                    )
                    cursor_pt = (tx, ty)
                elif action in ("wheel", "scroll"):
                    dx = float(args.get("delta_x", 0))
                    dy = float(args.get("delta_y", args.get("scroll_y", 120)))
                    client.call(
                        "Input.dispatchMouseEvent",
                        {"type": "mouseWheel", "x": vx, "y": vy, "deltaX": dx, "deltaY": dy},
                        session_id=sid,
                    )
                else:
                    self._dispatch_mouse_click(client, sid, vx, vy, button=btn, click_count=1)

                return (
                    {
                        "content": [{"type": "text", "text": f"pointer {action} at {cursor_pt} in {tab_key} (CDP rescue)"}],
                        "structuredContent": {"status": "ok", "tab_id": tab_key, **updated_meta},
                    },
                    cursor_pt,
                    updated_meta,
                )

            if tool_name == "browser_type":
                ref = args.get("ref")
                if isinstance(ref, str) and ref:
                    with self._lock:
                        rinfo = (self._tab_refs.get(tab_key) or {}).get(ref)
                    if rinfo and rinfo.get("x") is not None and rinfo.get("y") is not None:
                        self._dispatch_mouse_click(client, sid, float(rinfo["x"]), float(rinfo["y"]))
                        cursor_pt = (float(rinfo["x"]), float(rinfo["y"]))
                        time.sleep(0.05)
                        if args.get("clear"):
                            js_clr = (
                                f"(() => {{ const el = document.querySelector('[data-cua-rescue-ref={json.dumps(ref)}]'); "
                                "if (el && 'value' in el) { el.value = ''; el.dispatchEvent(new Event('input', {bubbles:true})); } })()"
                            )
                            client.call("Runtime.evaluate", {"expression": js_clr}, session_id=sid)

                text = args.get("text")
                if isinstance(text, str) and text:
                    client.call("Input.insertText", {"text": text}, session_id=sid)

                key_name = str(args.get("key") or ("Enter" if args.get("press_enter") or args.get("submit") else "")).strip()
                if key_name:
                    low = key_name.lower()
                    if low in SPECIAL_KEYS:
                        k_val, k_code, vk = SPECIAL_KEYS[low]
                    else:
                        k_val, k_code, vk = (key_name, key_name, ord(key_name[0].upper()) if len(key_name) == 1 else 0)
                    client.call(
                        "Input.dispatchKeyEvent",
                        {"type": "rawKeyDown", "key": k_val, "code": k_code, "windowsVirtualKeyCode": vk},
                        session_id=sid,
                    )
                    client.call(
                        "Input.dispatchKeyEvent",
                        {"type": "keyUp", "key": k_val, "code": k_code, "windowsVirtualKeyCode": vk},
                        session_id=sid,
                    )

                return (
                    {
                        "content": [{"type": "text", "text": f"typed input into {tab_key} (CDP rescue)"}],
                        "structuredContent": {"status": "ok", "tab_id": tab_key, **updated_meta},
                    },
                    cursor_pt,
                    updated_meta,
                )

            raise ValueError(f"Unsupported CDP rescue tool: {tool_name}")
        finally:
            try:
                client.call("Target.detachFromTarget", {"sessionId": sid}, timeout=1.5)
            except Exception:
                pass
            client.close()
