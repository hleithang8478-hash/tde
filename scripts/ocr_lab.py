# -*- coding: utf-8 -*-
"""OCR 调参试验台。

用途：
- 给一张本地截图（PNG/JPG），按你设的多组「预处理 + Tesseract 参数」跑一遍，输出每组结果与得分；
- 同时把每组**预处理后的图**保存到 logs/ocr_lab/，便于人工对比裁区是否合适、字是否够大、对比是否够；
- 另：可临时直接调用屏幕区域截图（--bbox left,top,width,height），跑一次现场 OCR。

示例：
  # 最常用：对截图跑「项目内多策略」 + 一组自定义参数
  python scripts\ocr_lab.py --image D:\test\panel.png --lang chi_sim+eng

  # 自定义一组参数（与 cmd 调 tesseract 等价）
  python scripts\ocr_lab.py --image D:\test\panel.png --lang chi_sim --psm 6 --extra "-c preserve_interword_spaces=1 -c tessedit_do_invert=0"

  # 直接截屏一片做实验（持仓表区）
  python scripts\ocr_lab.py --bbox 714,853,495,1834 --lang chi_sim+eng --target-edge 1200 --deskew

注意：和柜台运行同一台机执行，需装好 Tesseract（含 chi_sim）。
"""

from __future__ import annotations

import argparse
import base64
import io
import os
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.rpa.vision_inspector import (  # noqa: E402  (path-injected)
    _ocr_best_for_trade_panel,
    _ocr_png_base64_to_text,
    _ocr_signal_strength,
    _preprocess_pil_for_ocr,
)


def _read_image_to_b64(path: str) -> str:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"图片不存在：{p}")
    raw = p.read_bytes()
    return base64.b64encode(raw).decode("ascii")


def _grab_screen_to_b64(bbox_str: str) -> str:
    parts = [int(x) for x in bbox_str.split(",")]
    if len(parts) != 4:
        raise SystemExit("--bbox 须形如 left,top,width,height（整屏坐标）")
    left, top, w, h = parts
    try:
        from PIL import ImageGrab
    except Exception as e:
        raise SystemExit(f"截图需要 pillow：pip install pillow（{e}）") from e
    img = ImageGrab.grab(bbox=(left, top, left + w, top + h), all_screens=True)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _save_png(b64: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(b64))


def _decode_pil(b64: str):
    from PIL import Image

    raw = base64.b64decode(b64)
    img = Image.open(io.BytesIO(raw))
    return img.convert("RGB") if img.mode not in ("RGB", "L") else img


def _save_preprocessed_variants(b64: str, out_dir: Path, *, deskew: bool, target_min_edge: Optional[int]) -> None:
    """落盘 raw / mild / aggressive 三种预处理后的图，便于人工核对。"""
    img = _decode_pil(b64)
    out_dir.mkdir(parents=True, exist_ok=True)
    img.save(str(out_dir / "01_raw.png"))

    try:
        mild = _preprocess_pil_for_ocr(img, mode="mild", deskew=deskew, target_min_edge=target_min_edge)
        mild.save(str(out_dir / "02_mild.png"))
    except Exception as e:
        print(f"[ocr_lab] mild 预处理保存失败：{e}")

    try:
        aggr = _preprocess_pil_for_ocr(img, mode="aggressive", deskew=deskew, target_min_edge=target_min_edge)
        aggr.save(str(out_dir / "03_aggressive.png"))
    except Exception as e:
        print(f"[ocr_lab] aggressive 预处理保存失败：{e}")


def _print_block(title: str, body: str) -> None:
    print()
    print(f"========== {title} ==========")
    print(body)
    print(f"---------- /{title} ----------")


def _run_single(b64: str, *, lang: str, tess_cmd: str, cfg: str, mode: str,
                deskew: bool, target_edge: Optional[int]) -> Tuple[str, int]:
    txt = _ocr_png_base64_to_text(
        b64,
        lang=lang,
        tesseract_cmd=tess_cmd,
        preprocess=mode != "none",
        preprocess_mode=mode if mode != "none" else "mild",
        tesseract_config=cfg,
        preprocess_deskew=deskew,
        preprocess_target_min_edge=target_edge,
    )
    return txt, _ocr_signal_strength(txt)


def main() -> None:
    ap = argparse.ArgumentParser(description="OCR 调参试验台")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--image", help="本地 PNG/JPG 路径")
    src.add_argument("--bbox", help="实时截屏区 left,top,width,height（整屏坐标）")

    ap.add_argument("--lang", default="chi_sim+eng", help="Tesseract 语言（默认 chi_sim+eng）")
    ap.add_argument("--tess-cmd", default="", help="tesseract.exe 路径（PATH 找得到则不填）")

    ap.add_argument("--psm", type=int, default=None, help="自定义一组 psm（与 --oem 3 合并，单独跑一组）")
    ap.add_argument("--extra", default="", help="附加到该组的参数串（如 '-c preserve_interword_spaces=1'）")
    ap.add_argument("--mode", choices=["none", "mild", "aggressive"], default="aggressive",
                    help="自定义那组用哪种预处理（默认 aggressive）")

    ap.add_argument("--deskew", action="store_true", help="预处理时做 OpenCV 轻微纠斜")
    ap.add_argument("--target-edge", type=int, default=None,
                    help="将截图短边放大到至少 N 像素（建议 800~1500 试）")

    ap.add_argument("--no-multi", action="store_true", help="不跑项目内多策略，只跑自定义那组")
    ap.add_argument("--out-dir", default=str(Path(PROJECT_ROOT) / "logs" / "ocr_lab"),
                    help="预处理图与结果文本输出目录")

    args = ap.parse_args()

    out_dir = Path(args.out_dir) / time.strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.image:
        b64 = _read_image_to_b64(args.image)
        src_label = f"image={args.image}"
    else:
        b64 = _grab_screen_to_b64(args.bbox)
        src_label = f"bbox={args.bbox}"

    _save_png(b64, out_dir / "00_input.png")
    _save_preprocessed_variants(
        b64, out_dir, deskew=args.deskew, target_min_edge=args.target_edge
    )
    print(f"[ocr_lab] 输入与预处理图已保存：{out_dir}")
    print(f"[ocr_lab] 来源 {src_label}  lang={args.lang}  deskew={args.deskew}  target_edge={args.target_edge}")

    summary_lines: List[str] = [
        f"# OCR Lab Result @ {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"source: {src_label}",
        f"lang={args.lang}  deskew={args.deskew}  target_edge={args.target_edge}",
        "",
    ]

    if args.psm is not None:
        cfg = f"--oem 3 --psm {args.psm}".strip()
        if args.extra.strip():
            cfg = (cfg + " " + args.extra.strip()).strip()
        try:
            txt, sc = _run_single(
                b64,
                lang=args.lang,
                tess_cmd=args.tess_cmd,
                cfg=cfg,
                mode=args.mode,
                deskew=args.deskew,
                target_edge=args.target_edge,
            )
        except Exception as e:
            txt, sc = f"<异常：{e}>", -1
        title = f"自定义 mode={args.mode}  cfg={cfg}"
        _print_block(title, f"score={sc}\nchars={len(txt)}\n----\n{txt[:1200]}")
        summary_lines += [f"## {title}", f"score={sc}  chars={len(txt)}", "```", txt[:4000], "```", ""]

    if not args.no_multi:
        # 触发与运行时一致的「多策略」（包含 mild/raw/aggr × psm 6/11/12/3）
        # 这里通过 pipeline_cfg 透传额外开关
        pipeline_cfg = {
            "vlm_tesseract_extra_args": args.extra.strip(),
            "vlm_tesseract_preprocess_deskew": args.deskew,
            "vlm_tesseract_preprocess_target_min_edge": args.target_edge,
        }
        base_cfg = "--oem 3 --psm 6"
        try:
            best_txt, best_tag, best_sc = _ocr_best_for_trade_panel(
                b64,
                lang=args.lang,
                tess_cmd=args.tess_cmd,
                user_tesseract_config=base_cfg,
                include_aggressive=True,
                pipeline_cfg=pipeline_cfg,
            )
        except Exception as e:
            best_txt, best_tag, best_sc = f"<异常：{e}>", "ERROR", -1
        title = f"项目多策略最佳  strategy={best_tag}"
        _print_block(title, f"score={best_sc}\nchars={len(best_txt)}\n----\n{best_txt[:1200]}")
        summary_lines += [f"## {title}", f"score={best_sc}  chars={len(best_txt)}",
                          "```", best_txt[:4000], "```", ""]

    (out_dir / "result.md").write_text("\n".join(summary_lines), encoding="utf-8")
    print(f"\n[ocr_lab] 全部结果已写入：{out_dir / 'result.md'}")
    print("[ocr_lab] 调参建议：先看 03_aggressive.png 文字是否清晰、笔画是否粘连或断裂；")
    print("          字偏小：加大 --target-edge（如 900 → 1200）；轻度倾斜：加 --deskew；")
    print("          表头多空格：加 --extra '-c preserve_interword_spaces=1'；")
    print("          深色底浅色字：加 --extra '-c tessedit_do_invert=0' 然后再试一次反向。")


if __name__ == "__main__":
    main()
