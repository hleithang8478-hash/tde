# -*- coding: utf-8 -*-
"""
模块 1：券商 Windows 客户端窗口与进程守护。

设计原则：
- 尽量使用 pywinauto 获取稳定 HWND；失败时用 ctypes Win32 兜底。
- 任何对外操作包在 try/except 中，避免 RPA 线程因单次失败整体崩溃。
- global_reset 为防乌龙指核心：先清空浮层再回到标准下单态。
"""

from __future__ import annotations

import ctypes
import re
import subprocess
import time
from typing import Any, Callable, Optional, Tuple

# ---- Win32 常量（避免依赖 win32con 包名冲突）----
SW_RESTORE = 9


class WindowController:
    """
    管理券商交易终端进程与主窗口焦点。

    配置项（来自 src.config.RPA_CONFIG）常见键：
    - broker_exe_path: 可执行文件完整路径，用于 ensure_process_alive 拉起进程
    - broker_process_name: 进程名，如 "xiadan.exe"（用于 tasklist 检测）
    - main_window_title_re: 主窗口标题正则，用于 pywinauto 连接
    """

    def __init__(self, cfg: dict[str, Any]):
        self._cfg = cfg or {}
        self._app: Any = None
        self._main_win: Any = None
        self._user32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None
        self._send_keys_impl: Optional[Callable[[str], None]] = None

    # ------------------------------------------------------------------
    # 内部：纯 Win32 置前（不依赖 pywinauto）
    # ------------------------------------------------------------------
    def _hwnd_foreground(self, hwnd: int) -> None:
        """Restore + SetForegroundWindow；失败仅打日志，不抛。"""
        if not self._user32 or not hwnd:
            return
        try:
            self._user32.ShowWindow(hwnd, SW_RESTORE)
            time.sleep(0.05)
            self._user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

    def _find_hwnd_by_title_regex(self, pattern: str) -> int:
        """枚举顶层窗口，标题匹配正则则返回 HWND。"""
        if not self._user32 or not pattern:
            return 0
        cre = re.compile(pattern)
        found: list[int] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def _cb(hwnd, _lparam):
            try:
                buf = ctypes.create_unicode_buffer(512)
                self._user32.GetWindowTextW(hwnd, buf, 512)
                title = buf.value or ""
                if cre.search(title):
                    found.append(int(hwnd))
            except Exception:
                pass
            return True

        try:
            self._user32.EnumWindows(_cb, 0)
        except Exception:
            return 0
        return found[0] if found else 0

    # ------------------------------------------------------------------
    # 进程守护
    # ------------------------------------------------------------------
    def ensure_process_alive(self) -> None:
        """
        若配置中指定了 broker_process_name，则用 tasklist 检测；
        未运行且配置了 broker_exe_path 则尝试启动。
        """
        name = (self._cfg.get("broker_process_name") or "").strip()
        exe = (self._cfg.get("broker_exe_path") or "").strip()
        if not name:
            return
        try:
            out = subprocess.check_output(
                ["tasklist", "/FI", f"IMAGENAME eq {name}"],
                stderr=subprocess.STDOUT,
                timeout=8,
                creationflags=subprocess.CREATE_NO_WINDOW
                if hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0,
            )
            text = out.decode("gbk", errors="ignore").upper()
            if name.upper() in text:
                return
        except Exception:
            # tasklist 失败时不贸然启动第二份进程
            return

        if not exe:
            raise RuntimeError(
                f"券商进程未检测到 ({name})，且未配置 broker_exe_path，无法自动拉起。"
                "请先手动打开客户端，或在配置中填写正确的 broker_exe_path。"
            )

        if not bool(self._cfg.get("broker_auto_start", True)):
            raise RuntimeError(
                f"未检测到券商进程 ({name})，且已关闭 broker_auto_start。"
                "请先手动打开并登录客户端，再运行 EMS。"
            )

        try:
            subprocess.Popen([exe], shell=False)
            time.sleep(float(self._cfg.get("broker_start_wait_sec", 3.0)))
        except PermissionError as e:
            raise RuntimeError(
                "启动券商客户端时出现「拒绝访问」(WinError 5)。可按下面顺序处理：\n"
                "1) 推荐：先手动打开并登录客户端；把 RPA_CONFIG 里的 broker_exe_path 改成空字符串，"
                "只根据进程名检测、不自动拉起 exe，可避免权限问题。\n"
                "2) 确认 broker_exe_path 是完整的 .exe 路径，不要填文件夹或无效快捷方式。\n"
                "3) 用「以管理员身份运行」打开 PowerShell/CMD，再执行 python scripts\\run_ems.py。\n"
                "4) 检查杀毒软件是否拦截 Python 启动该 exe。\n"
                "5) 或在 RPA_CONFIG 增加 \"broker_auto_start\": False，强制仅手动启动客户端。\n"
                f"原始错误: {e}"
            ) from e
        except OSError as e:
            if getattr(e, "winerror", None) == 5:
                raise RuntimeError(
                    "启动券商客户端被拒绝访问(WinError 5)。"
                    "建议清空 broker_exe_path 并先手动打开客户端，或参见 broker_auto_start / 管理员运行说明。"
                    f"\n原始错误: {e}"
                ) from e
            raise RuntimeError(f"启动券商客户端失败: {e}") from e
        except Exception as e:
            raise RuntimeError(f"启动券商客户端失败: {e}") from e

    # ------------------------------------------------------------------
    # pywinauto 连接主窗
    # ------------------------------------------------------------------
    def _connect_pywinauto(self) -> bool:
        """连接主窗口；成功则缓存 self._main_win。"""
        try:
            from pywinauto import Application  # type: ignore
        except Exception:
            self._app = None
            self._main_win = None
            return False

        title_re = (self._cfg.get("main_window_title_re") or "").strip()
        backend = (self._cfg.get("pywinauto_backend") or "uia").strip()
        if not title_re:
            return False
        try:
            app = Application(backend=backend).connect(title_re=title_re, timeout=5)
            win = app.window(title_re=title_re)
            win.wait("exists ready", timeout=10)
            self._app = app
            self._main_win = win
            return True
        except Exception:
            self._app = None
            self._main_win = None
            return False

    # ------------------------------------------------------------------
    # 对外 API
    # ------------------------------------------------------------------
    def bring_to_front(self) -> None:
        """
        将券商主窗置前：优先 pywinauto.set_focus；
        失败则按标题正则 EnumWindows + SetForegroundWindow。
        """
        self.ensure_process_alive()
        ok = self._connect_pywinauto()
        try:
            if ok and self._main_win is not None:
                try:
                    if hasattr(self._main_win, "restore"):
                        self._main_win.restore()
                except Exception:
                    pass
                self._main_win.set_focus()
                time.sleep(0.12)
                return
        except Exception:
            pass

        hwnd = self._find_hwnd_by_title_regex(
            self._cfg.get("main_window_title_re") or "."
        )
        if hwnd:
            self._hwnd_foreground(hwnd)
            time.sleep(0.12)

    def global_reset(self, action: str) -> None:
        """
        防呆：连续 ESC 关闭浮层，再按 F1(买)/F2(卖) 进入标准快捷状态。

        :param action: "BUY" 或 "SELL"
        """
        try:
            for _ in range(3):
                self.send_keys("{ESC}")
                time.sleep(0.08)
            if str(action).upper() == "BUY":
                self.send_keys("{F1}")
            else:
                self.send_keys("{F2}")
            time.sleep(float(self._cfg.get("after_reset_wait_sec", 0.35)))
        finally:
            # 即使按键失败，也尝试再次置前，避免焦点落在错误窗口
            try:
                self.bring_to_front()
            except Exception:
                pass

    def send_keys(self, seq: str) -> None:
        """
        发送 pywinauto 风格按键序列，如 ``{F3}``、``{ESC}``、``^a``。
        不主动 bring_to_front，由调用方保证焦点在券商窗。
        """
        if self._send_keys_impl is None:
            self._send_keys_impl = self._make_send_keys()
        self._send_keys_impl(seq)

    @staticmethod
    def _coerce_braced_hotkey(spec: str, default_braced: str) -> str:
        s = (spec or "").strip()
        if s.startswith("{") and s.endswith("}"):
            return s
        u = s.upper().replace("{", "").replace("}", "")
        if re.fullmatch(r"F\d{1,2}", u):
            return "{" + u + "}"
        return default_braced

    def press_orders_hotkey(self) -> None:
        """当日委托（默认 {F3}，RPA_CONFIG.orders_refresh_hotkey 可写 F3 或 {F3}）。"""
        spec = self._coerce_braced_hotkey(
            str(self._cfg.get("orders_refresh_hotkey") or ""),
            "{F3}",
        )
        self.send_keys(spec)

    def press_trades_hotkey(self) -> None:
        """当日成交（默认 {F4}）。"""
        spec = self._coerce_braced_hotkey(
            str(self._cfg.get("trades_refresh_hotkey") or ""),
            "{F4}",
        )
        self.send_keys(spec)

    def get_window_rect_screen(self) -> Optional[Tuple[int, int, int, int]]:
        """主窗屏幕矩形 ``(left, top, right, bottom)``；失败返回 None。"""
        ok = self._connect_pywinauto()
        try:
            if ok and self._main_win is not None:
                r = self._main_win.rectangle()
                return (int(r.left), int(r.top), int(r.right), int(r.bottom))
        except Exception:
            pass
        hwnd = self._find_hwnd_by_title_regex(self._cfg.get("main_window_title_re") or ".")
        if not hwnd or not self._user32:
            return None

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        rect = RECT()
        try:
            if self._user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
        except Exception:
            pass
        return None

    def capture_window_bgr(self) -> Optional[Tuple[Any, Tuple[int, int]]]:
        """
        截取主窗区域为 BGR 图像（numpy）及左上角屏幕坐标 ``(left, top)``。
        """
        rect = self.get_window_rect_screen()
        if not rect:
            return None
        l, t, r, b = rect
        try:
            from PIL import ImageGrab  # type: ignore
            import numpy as np  # type: ignore
        except Exception:
            return None
        try:
            img = ImageGrab.grab(bbox=(l, t, r, b), all_screens=True)
            rgb = np.asarray(img.convert("RGB"), dtype=np.uint8)
            bgr = rgb[:, :, ::-1].copy()
            return bgr, (l, t)
        except Exception:
            return None

    def _make_send_keys(self) -> Callable[[str], None]:
        """优先 pywinauto.type_keys；否则 pyautogui.press 序列。"""
        try:
            from pywinauto.keyboard import send_keys  # type: ignore

            def _sk(seq: str):
                send_keys(seq, pause=0.02)

            return _sk
        except Exception:
            pass
        try:
            import pyautogui  # type: ignore

            def _sk2(seq: str):
                m = {
                    "{ESC}": "escape",
                    "{F1}": "f1",
                    "{F2}": "f2",
                    "{F3}": "f3",
                    "{F4}": "f4",
                }
                key = m.get(seq.strip().upper(), seq.strip("{}").lower())
                pyautogui.press(key)

            return _sk2
        except Exception:

            def _noop(_seq: str):
                raise RuntimeError("未安装 pywinauto / pyautogui，无法发送按键")

            return _noop
