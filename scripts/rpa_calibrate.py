#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RPA 模板标定助手：全屏半透明拖拽选区 → 保存 ``src/rpa/assets/{name}.png``，
并将相对主窗的矩形写入 ``src/rpa/assets/registry.json``（运行时被合并进 ``RPA_CONFIG['opencv_templates']``）。

使用前请登录并显示券商主窗口；脚本会先尝试将主窗置前。

用法（在项目根目录）::

    python scripts/rpa_calibrate.py
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import List, Optional, Tuple


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _run_drag_overlay() -> Optional[Tuple[int, int, int, int]]:
    """返回屏幕坐标系下的选区 ``(left, top, right, bottom)``；取消则 None。"""
    import tkinter as tk  # type: ignore

    result: List[Optional[Tuple[int, int, int, int]]] = [None]
    start: List[Optional[Tuple[int, int]]] = [None]
    rect_id: List[Optional[int]] = [None]

    root = tk.Tk()
    root.title("RPA 标定 — 拖拽矩形，Esc 取消")
    root.attributes("-fullscreen", True)
    root.attributes("-topmost", True)
    try:
        root.attributes("-alpha", 0.28)
    except Exception:
        pass
    root.configure(cursor="crosshair", bg="#202020")

    canvas = tk.Canvas(root, highlightthickness=0, bg="#202020")
    canvas.pack(fill=tk.BOTH, expand=True)

    def on_press(e: tk.Event) -> None:
        start[0] = (e.x_root, e.y_root)
        if rect_id[0] is not None:
            canvas.delete(rect_id[0])
            rect_id[0] = None

    def on_drag(e: tk.Event) -> None:
        if not start[0]:
            return
        x0, y0 = start[0]
        x1, y1 = e.x_root, e.y_root
        if rect_id[0] is not None:
            canvas.delete(rect_id[0])
        rx0 = min(x0, x1) - root.winfo_rootx()
        ry0 = min(y0, y1) - root.winfo_rooty()
        rx1 = max(x0, x1) - root.winfo_rootx()
        ry1 = max(y0, y1) - root.winfo_rooty()
        rect_id[0] = canvas.create_rectangle(rx0, ry0, rx1, ry1, outline="#00ff88", width=2)

    def on_release(e: tk.Event) -> None:
        if not start[0]:
            return
        x0, y0 = start[0]
        x1, y1 = e.x_root, e.y_root
        left, right = sorted((x0, x1))
        top, bottom = sorted((y0, y1))
        if right - left < 4 or bottom - top < 4:
            start[0] = None
            return
        result[0] = (left, top, right, bottom)
        root.destroy()

    def on_escape(_: tk.Event) -> None:
        result[0] = None
        root.destroy()

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    root.bind("<Escape>", on_escape)
    root.focus_force()
    root.mainloop()
    return result[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="RPA OpenCV 模板标定")
    parser.add_argument(
        "--project-root",
        type=Path,
        default=None,
        help="工程根目录（默认：本脚本上级目录）",
    )
    args = parser.parse_args()
    root = Path(args.project_root).resolve() if args.project_root else _project_root()
    sys.path.insert(0, str(root))
    os.chdir(str(root))

    from PIL import ImageGrab  # type: ignore

    from src.config import RPA_CONFIG
    from src.rpa.template_registry import ensure_assets_dir, template_png_path, write_registry_merge
    from src.rpa.window_controller import WindowController

    print("=== RPA 模板标定 ===")
    print("1) 请切换到券商客户端并显示主窗口。")
    input("2) 准备好后按回车：将尝试主窗置前并开始框选…")

    wc = WindowController(RPA_CONFIG if isinstance(RPA_CONFIG, dict) else {})
    try:
        wc.bring_to_front()
    except Exception as e:
        print(f"[WARN] bring_to_front: {e}")

    win_rect = wc.get_window_rect_screen()
    if not win_rect:
        print("[ERROR] 无法获取主窗矩形，请检查 RPA_CONFIG.main_window_title_re。")
        return 2
    wl, wt, wr, wb = win_rect
    print(f"当前主窗屏幕矩形: left={wl} top={wt} right={wr} bottom={wb}")

    print("\n全屏将出现半透明层：按住左键拖拽框选控件区域，松开完成；Esc 取消。")
    input("按回车开始框选…")
    sel = _run_drag_overlay()
    if not sel:
        print("已取消。")
        return 1
    left, top, right, bottom = sel
    w, h = right - left, bottom - top

    img = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
    ensure_assets_dir()

    raw_name = input("请输入模板名称（建议字母数字下划线，如 withdraw_btn / buy_confirm_btn）: ").strip()
    out_path = template_png_path(raw_name)
    name = out_path.stem
    if not name:
        print("[ERROR] 名称无效。")
        return 2
    img.save(str(out_path), format="PNG")
    print(f"已保存模板: {out_path}")

    rel_left = left - wl
    rel_top = top - wt
    meta = {
        "rel_left": int(rel_left),
        "rel_top": int(rel_top),
        "width": int(w),
        "height": int(h),
    }
    write_registry_merge(name, meta)
    if raw_name != name:
        print(f"[INFO] 文件名已规范为: {name}.png")
    print(f"已写入 registry 元数据: {meta}（相对主窗左上角）")

    print(
        "\n可选：在 src/config.py 的 RPA_CONFIG 中配置模板逻辑名（也可仅依赖 registry.json 自动合并）：\n"
        '    "opencv_confirm_template": "buy_confirm_btn",\n'
        '    "opencv_withdraw_template": "withdraw_btn",\n'
        '    "opencv_secondary_confirm_template": "secondary_confirm_btn",\n'
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
