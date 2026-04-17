# -*- coding: utf-8 -*-
"""下单信号要素校验（与柜台 RPA 无关的纯逻辑）。"""

from __future__ import annotations

import re
from typing import Any, Dict

_STOCK_RE = re.compile(r"^\d{6}\.(SH|SZ)$", re.IGNORECASE)


def assert_order_elements_ready(signal: Dict[str, Any]) -> None:
    """
    校验一条待执行信号是否具备发单要素；不齐则 ValueError（中文说明）。

    要求：
    - 证券代码、signal_type、price_type
    - 限价须 limit_price
    - 市价不要 limit_price（可有 None）
    - quantity_mode=ABSOLUTE 须 volume>0
    - quantity_mode=TARGET_PCT 须 weight_pct 在 (0, 100]
    - ORDER 须 action BUY/SELL
    """
    stock_code = str(signal.get("stock_code") or "").strip().upper()
    if not _STOCK_RE.match(stock_code):
        raise ValueError("证券代码格式须为 6 位.交易所后缀，如 002185.SZ")

    st = str(signal.get("signal_type") or "").strip().upper()
    if st == "UI_READ":
        p = str(signal.get("ui_panel") or "").strip().lower()
        if p not in ("orders", "trades", "funds", "positions"):
            raise ValueError("UI_READ 须设置 ui_panel 为 orders、trades、funds 或 positions")
        sc = str(signal.get("stock_code") or "").strip().upper()
        if not _STOCK_RE.match(sc):
            raise ValueError("证券代码格式须为 6 位.交易所后缀；UI_READ 可填占位 000000.SZ")
        return

    if st not in ("TARGET", "ORDER"):
        raise ValueError("signal_type 须为 ORDER、TARGET 或 UI_READ")

    pt = str(signal.get("price_type") or "MARKET").strip().upper()
    if pt not in ("MARKET", "LIMIT"):
        raise ValueError("price_type 须为 MARKET 或 LIMIT")

    if pt == "LIMIT":
        lp = signal.get("limit_price")
        if lp in (None, ""):
            raise ValueError("限价单必须提供 limit_price")
        try:
            if float(lp) <= 0:
                raise ValueError("limit_price 须大于 0")
        except (TypeError, ValueError) as e:
            raise ValueError("limit_price 须为有效数字") from e

    qm = str(signal.get("quantity_mode") or "ABSOLUTE").strip().upper()
    if qm not in ("ABSOLUTE", "TARGET_PCT"):
        raise ValueError("quantity_mode 须为 ABSOLUTE（绝对股数）或 TARGET_PCT（目标仓位百分比）")

    if qm == "ABSOLUTE":
        try:
            vol = int(signal.get("volume"))
        except (TypeError, ValueError) as e:
            raise ValueError("绝对数量模式下 volume 须为整数") from e
        if vol <= 0:
            raise ValueError("绝对数量模式下 volume 须大于 0")
    else:
        wp = signal.get("weight_pct")
        if wp in (None, ""):
            raise ValueError("目标仓位百分比模式下必须提供 weight_pct（例如 25 表示 25%）")
        try:
            p = float(wp)
        except (TypeError, ValueError) as e:
            raise ValueError("weight_pct 须为数字") from e
        if p <= 0 or p > 100:
            raise ValueError("weight_pct 须在 (0, 100] 之间")

    if st == "ORDER":
        act = str(signal.get("action") or "").strip().upper()
        if act not in ("BUY", "SELL"):
            raise ValueError("ORDER 模式下必须指定 action 为 BUY 或 SELL")
    elif st == "TARGET" and qm == "TARGET_PCT":
        act = str(signal.get("action") or "").strip().upper()
        if act not in ("BUY", "SELL"):
            raise ValueError("TARGET 目标仓位% 模式必须指定 action 为 BUY 或 SELL")
