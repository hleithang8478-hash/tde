# -*- coding: utf-8 -*-
"""
模块 3：安全输入引擎。

要点：
- 输入前 Ctrl+A + Backspace 清空，避免残留数字导致乌龙指。
- 数字/代码优先剪贴板粘贴，降低中文输入法干扰（可配置）。
- 六位代码输入后固定等待联想（默认 0.8s）。
"""

from __future__ import annotations

import time
from typing import Any, Callable


class RpaInputEngine:
    def __init__(self, cfg: dict[str, Any]):
        self._cfg = cfg or {}
        self._paste = bool(self._cfg.get("use_clipboard_paste", True))
        self._code_wait = float(self._cfg.get("code_autocomplete_wait_sec", 0.8))

    def _send_keys(self) -> Callable[[str], None]:
        try:
            from pywinauto.keyboard import send_keys  # type: ignore

            def _sk(seq: str):
                send_keys(seq, pause=0.02)

            return _sk
        except Exception:
            import pyautogui  # type: ignore

            def _sk2(seq: str):
                # 简化映射：仅覆盖本模块用到的组合键
                if seq.lower() == "^a":
                    pyautogui.hotkey("ctrl", "a")
                elif seq.lower() == "{bksp}" or seq.lower() == "{backspace}":
                    pyautogui.press("backspace")
                elif seq.lower() == "^v":
                    pyautogui.hotkey("ctrl", "v")
                elif seq.lower() == "{tab}":
                    pyautogui.press("tab")
                elif seq.lower() == "{enter}":
                    pyautogui.press("enter")
                else:
                    pyautogui.write(seq.replace("{", "").replace("}", ""), interval=0.02)

            return _sk2

    def safe_type(self, text: str) -> None:
        """
        清空后输入。若 use_clipboard_paste 且文本为「纯数字/小数点」则用 Ctrl+V。
        """
        sk = self._send_keys()
        text = str(text or "")
        try:
            sk("^a")
            time.sleep(0.04)
            sk("{BACKSPACE}")
            time.sleep(0.04)
            if self._paste and _is_numeric_like(text):
                try:
                    import pyperclip  # type: ignore

                    pyperclip.copy(text)
                    time.sleep(0.03)
                    sk("^v")
                except Exception:
                    sk(text)
            else:
                sk(text)
        finally:
            time.sleep(float(self._cfg.get("after_type_wait_sec", 0.05)))

    def type_stock_code_then_wait(self, code: str) -> None:
        """输入 6 位代码并等待联想/行情回填。"""
        digits = "".join(c for c in str(code) if c.isdigit())[:6]
        self.safe_type(digits)
        time.sleep(self._code_wait)

    def tab_next_field(self) -> None:
        sk = self._send_keys()
        sk("{TAB}")
        time.sleep(0.06)


def _is_numeric_like(s: str) -> bool:
    s = str(s).strip()
    if not s:
        return False
    for ch in s:
        if ch not in "0123456789.":
            return False
    return True
