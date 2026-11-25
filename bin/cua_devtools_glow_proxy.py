# -*- coding: utf-8 -*-
# 檔案用途：chrome-devtools MCP 透明視覺代理層（自動同步目標 Chrome 視窗的 Tab-Aware 同色圓角螢光外框與虛擬游標回饋）
# 架構原則：複用 WindowGlowOverlay 與 ProxyVirtualCursorOverlay，綁定 Win32 Job Object 防孤兒行程，全程不搶焦點與實體滑鼠。

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
import re
import shutil
import subprocess
import sys
import threading

_BIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _BIN_DIR not in sys.path:
    sys.path.insert(0, _BIN_DIR)

from cua_cdp_rescue import ProxyVirtualCursorOverlay
from cua_window_glow_proxy import (
    WNDENUMPROC,
    WindowGlowOverlay,
    bind_process_to_kill_on_close_job,
    resolve_cua_cursor_color,
    titles_match_browser_tab,
    user32,
)

PAGE_LINE_RE = re.compile(r"^\s*(\d+):\s+(.+?)\s+\(([^)\s]+)\)(\s+\[selected\])?\s*$")

POINTER_OR_INPUT_TOOLS = {
    "click",
    "drag",
    "fill",
    "fill_form",
    "hover",
    "press_key",
    "type_text",
    "upload_file",
    "handle_dialog",
}

PASSIVE_PAGE_TOOLS = {
    "navigate_page",
    "select_page",
    "new_page",
    "evaluate_script",
    "take_screenshot",
    "take_snapshot",
    "wait_for",
    "get_css_styles",
    "lighthouse_audit",
    "performance_start_trace",
    "performance_stop_trace",
}


class ChromeDevToolsGlowProxy:
    """為 chrome-devtools-mcp 提供與 cua-driver 完全一致的 Tab-Aware 同色圓角外框與虛擬游標回饋。"""

    def __init__(self, command_argv: list[str]) -> None:
        self.command_argv = self._resolve_command(command_argv)
        self.session_name = os.environ.get("CUA_DEVTOOLS_SESSION", "agy-cursor").strip() or "agy-cursor"
        self.overlay = WindowGlowOverlay()
        self.cursor_overlay = ProxyVirtualCursorOverlay(titles_match_browser_tab)

        self._lock = threading.Lock()
        self._stdout_lock = threading.Lock()
        self._stdin_lock = threading.Lock()

        self._pages: dict[int, dict[str, str]] = {}
        self._selected_page_id: int = 0
        self._req_meta: dict[object, dict] = {}
        self._proc: subprocess.Popen | None = None
        self._job_handle = None

    @staticmethod
    def _resolve_command(argv: list[str]) -> list[str]:
        if not argv:
            argv = ["npx", "-y", "chrome-devtools-mcp@latest", "--autoConnect"]
        exe = argv[0]
        if os.name == "nt" and not os.path.isabs(exe):
            found = shutil.which(f"{exe}.cmd") or shutil.which(f"{exe}.exe") or shutil.which(exe)
            if found:
                return [found, *argv[1:]]
        return list(argv)

    def _enumerate_visible_chrome_windows(self) -> list[dict]:
        results: list[dict] = []
        if os.name != "nt":
            return results
        cls_buf = ctypes.create_unicode_buffer(64)
        title_buf = ctypes.create_unicode_buffer(512)

        def _cb(hwnd: int, _lparam: int) -> bool:
            h_int = int(hwnd or 0)
            if h_int <= 0 or not user32.IsWindowVisible(wintypes.HWND(h_int)):
                return True
            cn_len = user32.GetClassNameW(wintypes.HWND(h_int), cls_buf, 64)
            if (cls_buf.value if cn_len > 0 else "") != "Chrome_WidgetWin_1":
                return True
            t_len = user32.GetWindowTextW(wintypes.HWND(h_int), title_buf, 512)
            raw_title = title_buf.value if t_len > 0 else ""
            if not raw_title:
                return True
            if not user32.IsIconic(wintypes.HWND(h_int)):
                bounds = WindowGlowOverlay._get_target_bounds(h_int)
                if not bounds or bounds[0] < -16000 or bounds[1] < -16000 or bounds[2] < 80 or bounds[3] < 80:
                    return True
            results.append(
                {
                    "hwnd": h_int,
                    "raw_title": raw_title,
                    "is_iconic": bool(user32.IsIconic(wintypes.HWND(h_int))),
                    "is_browser": "google chrome" in raw_title.lower(),
                }
            )
            return True

        cb_func = WNDENUMPROC(_cb)
        try:
            user32.EnumWindows(cb_func, 0)
        except Exception:
            pass
        return results

    def _resolve_chrome_hwnd_and_title(self, page_id: int | None) -> tuple[int, str]:
        with self._lock:
            pid = page_id if (isinstance(page_id, int) and page_id > 0) else self._selected_page_id
            pinfo = dict(self._pages.get(pid) or {})
        tab_title = (pinfo.get("title") or "").strip()

        wins = self._enumerate_visible_chrome_windows()
        if not wins:
            return (0, tab_title)

        # 1. 優先尋找目前正停留於該分頁標題的可見 Chrome 視窗
        if tab_title:
            best_hwnd = 0
            best_score = 0
            for w in wins:
                sc = titles_match_browser_tab(w["raw_title"], tab_title)
                if sc > best_score:
                    best_score = sc
                    best_hwnd = w["hwnd"]
            if best_hwnd > 0:
                return (best_hwnd, tab_title)

        # 2. 若該分頁目前在背景（使用者正看別的分頁），綁定最上層非最小化主 Chrome 視窗並附帶 expected_tab_title（切回時即亮起）
        browser_wins = [w for w in wins if w["is_browser"] and not w["is_iconic"]] or [
            w for w in wins if not w["is_iconic"]
        ] or wins
        return (int(browser_wins[0]["hwnd"]), tab_title)

    def _parse_pages_from_text(self, text: str) -> None:
        if "## Pages" not in text:
            return
        parsed: dict[int, dict[str, str]] = {}
        sel_id = 0
        in_pages = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped == "## Pages":
                in_pages = True
                continue
            if in_pages:
                if stripped.startswith("## "):
                    break
                m = PAGE_LINE_RE.match(line)
                if m:
                    pid = int(m.group(1))
                    title = m.group(2).strip()
                    url = m.group(3).strip()
                    is_sel = bool(m.group(4))
                    parsed[pid] = {"title": title, "url": url}
                    if is_sel:
                        sel_id = pid
        if parsed:
            with self._lock:
                self._pages.update(parsed)
                if sel_id > 0:
                    self._selected_page_id = sel_id

    def _trigger_visual_feedback(self, tool_name: str, page_id: int | None) -> None:
        if tool_name not in POINTER_OR_INPUT_TOOLS and tool_name not in PASSIVE_PAGE_TOOLS:
            return
        hwnd, tab_title = self._resolve_chrome_hwnd_and_title(page_id)
        if hwnd <= 0:
            return
        is_nav = tool_name in ("navigate_page", "click", "new_page")
        self.overlay.highlight_window(
            hwnd,
            self.session_name,
            expected_tab_title=tab_title,
            is_navigation=is_nav,
            is_browser=True,
        )
        if tool_name in POINTER_OR_INPUT_TOOLS:
            rgb = resolve_cua_cursor_color(self.session_name)
            self.cursor_overlay.pulse_at_viewport(hwnd, rgb, expected_tab_title=tab_title)

    def _handle_client_line(self, line: bytes) -> None:
        try:
            msg = json.loads(line.decode("utf-8", errors="ignore"))
        except Exception:
            self._send_upstream(line)
            return

        if isinstance(msg, dict) and msg.get("method") == "tools/call":
            req_id = msg.get("id")
            params = msg.get("params") or {}
            tool_name = str(params.get("name") or "")
            args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            page_id = args.get("pageId") if isinstance(args.get("pageId"), int) else None

            if tool_name == "select_page" and isinstance(page_id, int) and page_id > 0:
                with self._lock:
                    self._selected_page_id = page_id

            if req_id is not None:
                with self._lock:
                    self._req_meta[req_id] = {"tool_name": tool_name, "page_id": page_id}

            self._trigger_visual_feedback(tool_name, page_id)

        self._send_upstream(line)

    def _handle_server_line(self, line: bytes) -> None:
        try:
            msg = json.loads(line.decode("utf-8", errors="ignore"))
        except Exception:
            self._send_client(line)
            return

        if isinstance(msg, dict):
            req_id = msg.get("id")
            meta = {}
            if req_id is not None:
                with self._lock:
                    meta = self._req_meta.pop(req_id, {})

            res = msg.get("result")
            if isinstance(res, dict):
                c_list = res.get("content")
                if isinstance(c_list, list):
                    full_text = "\n".join(
                        str(c.get("text") or "") for c in c_list if isinstance(c, dict) and c.get("type") == "text"
                    )
                    if full_text:
                        self._parse_pages_from_text(full_text)

                tool_name = str(meta.get("tool_name") or "")
                if tool_name:
                    self._trigger_visual_feedback(tool_name, meta.get("page_id"))

        self._send_client(line)

    def _send_upstream(self, raw: bytes) -> None:
        if self._proc and self._proc.stdin:
            with self._stdin_lock:
                self._proc.stdin.write(raw)
                self._proc.stdin.flush()

    def _send_client(self, raw: bytes) -> None:
        with self._stdout_lock:
            sys.stdout.buffer.write(raw)
            sys.stdout.buffer.flush()

    def run(self) -> int:
        creation_flags = 0x08000000 if os.name == "nt" else 0
        self._proc = subprocess.Popen(
            self.command_argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=sys.stderr,
            bufsize=0,
            creationflags=creation_flags,
        )
        if os.name == "nt":
            self._job_handle = bind_process_to_kill_on_close_job(int(self._proc._handle))

        def _reader() -> None:
            try:
                assert self._proc is not None and self._proc.stdout is not None
                while True:
                    line = self._proc.stdout.readline()
                    if not line:
                        break
                    self._handle_server_line(line)
            except Exception:
                pass

        t = threading.Thread(target=_reader, daemon=True)
        t.start()

        try:
            while True:
                line = sys.stdin.buffer.readline()
                if not line:
                    break
                self._handle_client_line(line)
        except Exception:
            pass
        finally:
            self.overlay.shutdown()
            self.cursor_overlay.shutdown()
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


def run_devtools_glow_proxy(cmd: list[str]) -> int:
    proxy = ChromeDevToolsGlowProxy(cmd)
    return proxy.run()


if __name__ == "__main__":
    sys.exit(run_devtools_glow_proxy(sys.argv[1:]))
