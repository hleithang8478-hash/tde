# -*- coding: utf-8 -*-
"""Ptrade 客户端内桥接模板（复制到 Ptrade 策略编辑器运行）

fill_config.ps1 会生成 scripts/ptrade_bridge_generated.py（替换 PROJECT_ROOT）。

注意：部分 Ptrade 环境禁止 import sys / import os。
用 ctypes 切换工作目录：Windows 用 SetCurrentDirectoryW；无 windll 时用 WinDLL('kernel32')；
Linux/部分内核用 libc.chdir（windll 不存在属正常情况）。
"""

import ctypes
import threading

# 由 scripts/windows/fill_config.ps1 生成 ptrade_bridge_generated.py 时替换该占位符
PROJECT_ROOT = r"{{PROJECT_ROOT_PLACEHOLDER}}"


def _looks_like_windows_abs_path(s):
    s = str(s)
    if len(s) >= 3 and s[1] == ":" and s[0].isalpha() and s[2] in ("\\", "/"):
        return True
    if s.startswith("\\\\"):
        return True
    return False


def _chdir_project_root(root):
    root = str(root or "").strip()
    if not root:
        raise RuntimeError("PROJECT_ROOT 为空，请重新运行 fill_config.ps1 生成桥接脚本。")

    # 1) 标准 Windows
    w = getattr(ctypes, "windll", None)
    if w is not None:
        ok = w.kernel32.SetCurrentDirectoryW(root)
        if ok:
            return
        err = ctypes.get_last_error()
        raise RuntimeError(
            "SetCurrentDirectoryW 失败 WinErr=%s path=%s" % (err, root)
        )

    # 2) 仍可能是 Windows，但 ctypes 无 windll（部分嵌入环境）
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        fn = k32.SetCurrentDirectoryW
        fn.argtypes = [ctypes.c_wchar_p]
        fn.restype = ctypes.c_bool
        if fn(root):
            return
        err = ctypes.get_last_error()
        raise RuntimeError(
            "SetCurrentDirectoryW(WinDLL) 失败 WinErr=%s path=%s" % (err, root)
        )
    except (OSError, AttributeError, TypeError, ValueError):
        pass

    # 3) POSIX（Linux/macOS 或远程 Notebook 内核）
    if _looks_like_windows_abs_path(root):
        raise RuntimeError(
            "当前 Python 是类 Unix 环境（无 Windows kernel32），不能使用盘符路径：\n  %s\n"
            "若在 Jupyter/Linux 里试跑，请把 PROJECT_ROOT 改成 Unix 路径（如 /path/to/trader）。\n"
            "山西证券 Ptrade 实盘请在 Windows 客户端策略里运行本脚本，并保持 PROJECT_ROOT 为 C:\\... 形式。"
            % (root,)
        )

    try:
        libc = ctypes.CDLL(None)
    except Exception as e:
        raise RuntimeError(
            "无法切换工作目录：无 windll/WinDLL，且 libc 不可用: %s path=%s"
            % (e, root)
        )
    libc.chdir.argtypes = [ctypes.c_char_p]
    libc.chdir.restype = ctypes.c_int
    rc = libc.chdir(root.encode("utf-8"))
    if rc != 0:
        raise RuntimeError("libc.chdir 失败 path=%s rc=%s" % (root, rc))


_chdir_project_root(PROJECT_ROOT)

from src.main import main
from src.ptrade_bridge_setup import collect_universe_codes


def initialize(context):
    log.info("正在启动 EMS 执行引擎…")

    codes = collect_universe_codes()
    if codes:
        try:
            set_universe(codes)
            log.info("set_universe 已设置 %s 只标的（静态池 + 可选库内去重）", len(codes))
        except Exception as e:
            log.error("set_universe 失败（请检查代码格式与 Ptrade 版本）: %s", e)
    else:
        log.warning(
            "股票池为空：请在 src.config.PTRADE_CONFIG['universe_static'] 配置标的，"
            "或启用 universe_include_db_distinct 并确保 trade_signals 中已有代码。"
        )

    ems_thread = threading.Thread(target=main, name="EMS-MainLoop", daemon=True)
    ems_thread.start()
    log.info("EMS 已在后台线程运行，轮询 trade_signals。")


def handle_data(context, data):
    pass
