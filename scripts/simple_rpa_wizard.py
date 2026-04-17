#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
逐项修改 RPA 相关配置：每改一条立即写回 ``src/config.py``（不重跑整套向导）。

用法::

    python scripts/simple_rpa_wizard.py
    python scripts/simple_rpa_wizard.py --project-root "C:\\软件\\trader"
    python scripts/simple_rpa_wizard.py --backup   # 每次写入前先备份 config.py

数据库 / API 等仍请用 ``scripts/configure_interactive.py`` 或手改 config。
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict

_snap_i = 0


def _load_config_io():
    p = Path(__file__).resolve().parent / "config_io.py"
    spec = importlib.util.spec_from_file_location("ems_config_io", p)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"找不到 config_io: {p}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_ci():
    sd = Path(__file__).resolve().parent
    if str(sd) not in sys.path:
        sys.path.insert(0, str(sd))
    import configure_interactive as ci  # noqa: E402

    return ci


def _reload_rpa(project_root: Path, cio: Any) -> Dict[str, Any]:
    global _snap_i
    _snap_i += 1
    cfg = project_root.resolve() / "src" / "config.py"
    m = cio.load_config_module(cfg, f"_ems_rpa_snap_{_snap_i}")
    return copy.deepcopy(dict(m.RPA_CONFIG))


def _reload_exec(project_root: Path, cio: Any) -> bool:
    global _snap_i
    _snap_i += 1
    cfg = project_root.resolve() / "src" / "config.py"
    m = cio.load_config_module(cfg, f"_ems_exec_snap_{_snap_i}")
    return bool(getattr(m, "EXECUTOR_SUBMIT_ONLY_MODE", True))


def _fmt_bbox(v: Any) -> str:
    if v is None:
        return "none"
    if isinstance(v, (list, tuple)) and len(v) == 4:
        return f"{v[0]},{v[1]},{v[2]},{v[3]}"
    return repr(v)


def _fmt_xy(v: Any) -> str:
    if v is None:
        return "none"
    if isinstance(v, (list, tuple)) and len(v) == 2:
        return f"{v[0]},{v[1]}"
    return repr(v)


def _print_header(root: Path, rpa: Dict[str, Any], exec_only: bool) -> None:
    print("\n" + "═" * 52)
    print("  简易配置（改一条存一条 → src/config.py）")
    print("═" * 52)
    print(f"  工程根: {root}")
    print(f"  当前: 下单前截图校验 ai_verify_enabled = {rpa.get('ai_verify_enabled')}")
    print(f"         纯鼠标流 order_ui_mouse_flow = {rpa.get('order_ui_mouse_flow')}")
    print(f"         下单前面板 bbox_pre_trade = {_fmt_bbox(rpa.get('bbox_pre_trade'))}")
    print(f"         买入按钮 confirm_button_xy = {_fmt_xy(rpa.get('confirm_button_xy'))}")
    print(f"         EXECUTOR_SUBMIT_ONLY_MODE = {exec_only}")
    print("─" * 52)


def _menu() -> None:
    print(
        """
  0 — 暂不填坐标：关闭「下单前 VLM 截图校验」（不关纯鼠标流）
  1 — 下单前是否做截图校验（ai_verify_enabled）y/n
  2 — 弹窗 VLM 解析失败是否阻断下单（strict_popup_ai）y/n
  3 — 下单前面板区域 bbox_pre_trade（左,上,宽,高 或 none）
  4 — 买入确认按钮中心 confirm_button_xy（x,y 或 none）
  5 — 卖出确认按钮 confirm_sell_button_xy（x,y 或 none；可与买入不同）
  6 — 二次确认「确定」中心 secondary_confirm_xy（x,y 或 none）
 35 — 委托成功后若再弹「确定」post_order_success_ok_xy（x,y 或 none；不配则跳过）
  7 — 当日委托表区域 bbox_order_table（左,上,宽,高 或 none）
  8 — 撤单按钮 withdraw_button_xy（x,y 或 none）
  9 — 纯鼠标填单 order_ui_mouse_flow（y/n；为 y 时需配齐 mf_*）
 10 — 窗口最大化按钮 window_maximize_button_xy（x,y 或 none）
 11 — 证券代码输入框 mf_stock_code_xy
 12 — 买入方向 mf_direction_buy_xy
 13 — 卖出方向 mf_direction_sell_xy
 14 — 限价（独立按钮，非下拉）mf_entrust_type_limit_xy
 15 — 市价（独立按钮，非下拉）mf_entrust_type_market_xy
 28 — 委托类型「下拉框」点击处 mf_entrust_type_dropdown_xy（配了则走下拉逻辑，忽略 14/15）
 29 — 下拉展开后点「限价」mf_entrust_type_limit_option_xy
 30 — 下拉展开后点「市价」mf_entrust_type_market_option_xy
 16 — 委托价格框 mf_price_xy
 17 — 数量类型「股」（独立按钮）mf_quantity_type_shares_xy
 18 — 数量类型「比例」（独立按钮）mf_quantity_type_ratio_xy
 31 — 数量类型「下拉框」mf_quantity_type_dropdown_xy（配了则走下拉，忽略 17/18）
 32 — 展开后「固定数量」mf_quantity_type_fixed_qty_option_xy（对应信号 ABSOLUTE）
 33 — 展开后「固定金额」mf_quantity_type_fixed_amount_option_xy（预留）
 34 — 展开后「持仓比例」mf_quantity_type_position_ratio_option_xy（对应 TARGET_PCT）
 19 — 数量输入框 mf_quantity_xy
 20 — 进程名 broker_process_name
 21 — 主窗口标题正则 main_window_title_re
 22 — 券商客户端 exe（留空则不自动启动）broker_exe_path
 23 — VLM 接口 URL（完整 …/v1/chat/completions）vlm_api_url
 24 — VLM API Key
 25 — VLM 模型名 vlm_model
 26 — 钉钉机器人 Webhook（不要则回车清空）
 27 — 递交后是否轮询委托（EXECUTOR_SUBMIT_ONLY_MODE）y=仅递交不轮询 / n=轮询查单

  h — 简要说明（坐标格式）
  q — 退出
"""
    )


def _run_choice(
    choice: str,
    root: Path,
    cio: Any,
    ci: Any,
    backup: bool,
) -> None:
    choice = choice.strip().lower()

    rpa = _reload_rpa(root, cio)

    def save() -> None:
        cio.write_project_config(root, rpa=rpa, backup=backup)
        print("[已保存] src/config.py")

    if choice == "h":
        print(
            """
坐标格式：
  · 区域 bbox：四个整数，英文逗号分隔：左,上,宽度,高度；没有填 none
  · 点 xy：两个整数 x,y；没有填 none
纯鼠标流不依赖「代码后按几次 Tab」；config 里 tabs_* 仅兼容旧 Tab 路径，本菜单不单独问。

委托类型若是下拉框：请配 28（点开下拉）+ 29（限价项）+ 30（市价项）；不要与 14/15 混用。
数量类型若是下拉框：请配 31 + 32/33/34；与 17/18 二选一。
"""
        )
        return

    if choice == "0":
        rpa["ai_verify_enabled"] = False
        save()
        print("已关闭 ai_verify_enabled。以后要校验请改项 1 为「是」，并填好项 3。")
        return

    if choice == "1":
        rpa["ai_verify_enabled"] = ci.ask_bool(
            "下单前是否启用 VLM/OCR 截图校验",
            not bool(rpa.get("ai_verify_enabled")),
        )
        save()
        return
    if choice == "2":
        rpa["strict_popup_ai"] = ci.ask_bool(
            "弹窗解析失败是否直接阻断",
            not bool(rpa.get("strict_popup_ai")),
        )
        save()
        return
    if choice == "3":
        raw = ci.ask("下单前面板区域 bbox_pre_trade", _fmt_bbox(rpa.get("bbox_pre_trade")))
        rpa["bbox_pre_trade"] = ci.parse_bbox(raw)
        save()
        return
    if choice == "4":
        raw = ci.ask("买入确认按钮中心 confirm_button_xy", _fmt_xy(rpa.get("confirm_button_xy")))
        rpa["confirm_button_xy"] = ci.parse_xy(raw)
        save()
        return
    if choice == "5":
        raw = ci.ask("卖出确认按钮 confirm_sell_button_xy", _fmt_xy(rpa.get("confirm_sell_button_xy")))
        rpa["confirm_sell_button_xy"] = ci.parse_xy(raw)
        save()
        return
    if choice == "6":
        raw = ci.ask("二次确认「确定」中心 secondary_confirm_xy", _fmt_xy(rpa.get("secondary_confirm_xy")))
        rpa["secondary_confirm_xy"] = ci.parse_xy(raw)
        save()
        return
    if choice == "35":
        raw = ci.ask(
            "委托递交成功后若再弹「确定」post_order_success_ok_xy（常与 secondary 同坐标）",
            _fmt_xy(rpa.get("post_order_success_ok_xy")),
        )
        rpa["post_order_success_ok_xy"] = ci.parse_xy(raw)
        save()
        return
    if choice == "7":
        raw = ci.ask("委托表区域 bbox_order_table", _fmt_bbox(rpa.get("bbox_order_table")))
        rpa["bbox_order_table"] = ci.parse_bbox(raw)
        save()
        return
    if choice == "8":
        raw = ci.ask("撤单按钮 withdraw_button_xy", _fmt_xy(rpa.get("withdraw_button_xy")))
        rpa["withdraw_button_xy"] = ci.parse_xy(raw)
        save()
        return
    if choice == "9":
        rpa["order_ui_mouse_flow"] = ci.ask_bool(
            "是否启用纯鼠标填单流程",
            not bool(rpa.get("order_ui_mouse_flow", True)),
        )
        save()
        return
    if choice == "10":
        raw = ci.ask("窗口最大化按钮 window_maximize_button_xy", _fmt_xy(rpa.get("window_maximize_button_xy")))
        rpa["window_maximize_button_xy"] = ci.parse_xy(raw)
        save()
        return

    def _xy_key(key: str, label: str) -> None:
        nonlocal rpa
        raw = ci.ask(label, _fmt_xy(rpa.get(key)))
        rpa[key] = ci.parse_xy(raw)
        save()

    xy_map = {
        "11": ("mf_stock_code_xy", "证券代码输入框 mf_stock_code_xy"),
        "12": ("mf_direction_buy_xy", "买入方向 mf_direction_buy_xy"),
        "13": ("mf_direction_sell_xy", "卖出方向 mf_direction_sell_xy"),
        "14": ("mf_entrust_type_limit_xy", "限价（独立按钮）mf_entrust_type_limit_xy"),
        "15": ("mf_entrust_type_market_xy", "市价（独立按钮）mf_entrust_type_market_xy"),
        "28": ("mf_entrust_type_dropdown_xy", "委托类型下拉框 mf_entrust_type_dropdown_xy"),
        "29": ("mf_entrust_type_limit_option_xy", "下拉展开后限价项 mf_entrust_type_limit_option_xy"),
        "30": ("mf_entrust_type_market_option_xy", "下拉展开后市价项 mf_entrust_type_market_option_xy"),
        "16": ("mf_price_xy", "委托价格框 mf_price_xy"),
        "17": ("mf_quantity_type_shares_xy", "数量类型「股」mf_quantity_type_shares_xy"),
        "18": ("mf_quantity_type_ratio_xy", "数量类型「比例」mf_quantity_type_ratio_xy"),
        "31": ("mf_quantity_type_dropdown_xy", "数量类型下拉框 mf_quantity_type_dropdown_xy"),
        "32": ("mf_quantity_type_fixed_qty_option_xy", "数量下拉-固定数量 mf_quantity_type_fixed_qty_option_xy"),
        "33": ("mf_quantity_type_fixed_amount_option_xy", "数量下拉-固定金额 mf_quantity_type_fixed_amount_option_xy"),
        "34": ("mf_quantity_type_position_ratio_option_xy", "数量下拉-持仓比例 mf_quantity_type_position_ratio_option_xy"),
        "19": ("mf_quantity_xy", "数量输入框 mf_quantity_xy"),
    }
    if choice in xy_map:
        k, lab = xy_map[choice]
        _xy_key(k, lab)
        return

    if choice == "20":
        d = rpa.get("broker_process_name") or "ptrade.exe"
        rpa["broker_process_name"] = ci.ask("进程名（任务管理器「名称」列）", str(d))
        save()
        return
    if choice == "21":
        d = rpa.get("main_window_title_re") or "PTrade.*交易"
        rpa["main_window_title_re"] = ci.ask("主窗口标题里不变的一段（正则）", str(d))
        save()
        return
    if choice == "22":
        d = rpa.get("broker_exe_path") or ""
        rpa["broker_exe_path"] = ci.ask("券商 exe 全路径（不自动启动则留空）", str(d))
        save()
        return
    if choice == "23":
        d = rpa.get("vlm_api_url") or ""
        rpa["vlm_api_url"] = ci.ask("VLM 接口 URL", str(d))
        save()
        return
    if choice == "24":
        d = rpa.get("vlm_api_key") or ""
        rpa["vlm_api_key"] = ci.ask("VLM API Key", str(d))
        save()
        return
    if choice == "25":
        d = rpa.get("vlm_model") or "deepseek-chat"
        rpa["vlm_model"] = ci.ask("VLM 模型名", str(d))
        save()
        return
    if choice == "26":
        d = rpa.get("dingtalk_webhook_url") or ""
        rpa["dingtalk_webhook_url"] = ci.ask("钉钉 Webhook（清空则回车）", str(d))
        save()
        return
    if choice == "27":
        # True = submit only；ask_bool 第二参「默认否」：此处用 not cur 使默认与当前配置一致
        cur = _reload_exec(root, cio)
        newv = ci.ask_bool("是否「仅递交、不轮询 query_order」", not cur)
        cio.write_project_config(root, rpa=rpa, exec_submit_only=newv, backup=backup)
        print("[已保存] EXECUTOR_SUBMIT_ONLY_MODE 与 src/config.py")
        return

    print("未知选项，请输入菜单前的数字或 q。")


def main() -> int:
    ap = argparse.ArgumentParser(description="简易 RPA 配置：逐项写入 src/config.py")
    ap.add_argument("--project-root", type=Path, default=None)
    ap.add_argument("--backup", action="store_true", help="每次写入前备份 config.py")
    args = ap.parse_args()

    ci = _load_ci()
    ci._configure_stdio_utf8()
    cio = _load_config_io()

    root = (args.project_root or Path(__file__).resolve().parent.parent).resolve()
    cfg = root / "src" / "config.py"
    if not cfg.is_file():
        print(f"[错误] 找不到 {cfg}", file=sys.stderr)
        return 2

    while True:
        rpa = _reload_rpa(root, cio)
        ex = _reload_exec(root, cio)
        _print_header(root, rpa, ex)
        _menu()
        line = input("请选择 [0–35 / h / q]: ").strip()
        if line.lower() in ("q", "quit", "exit"):
            print("再见。")
            break
        try:
            _run_choice(line, root, cio, ci, backup=bool(args.backup))
        except ValueError as e:
            print(f"[格式错误] {e}")
        except Exception as e:
            print(f"[错误] {type(e).__name__}: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
