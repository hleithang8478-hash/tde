# -*- coding: utf-8 -*-
"""信号接入层：负责统一解析并写入 trade_signals"""

import re
from typing import Dict, Any, Optional

from src.core.repository import SignalRepository


class SignalValidationError(ValueError):
    """信号格式错误"""


# 接入平面 /health 返回，用于核对云端是否已部署含 UI_READ 的校验逻辑（应为 2）
SIGNAL_SCHEMA_VERSION = 2

_STOCK_RE = re.compile(r"^\d{6}\.(SH|SZ)$", re.IGNORECASE)

# 底栏面板：与 RPA_CONFIG 中 panel_tab_* 一一对应
_CANON_UI_PANEL_ALIASES: Dict[str, str] = {
    "orders": "orders",
    "order": "orders",
    "entrust": "orders",
    "entrusts": "orders",
    "委托": "orders",
    "trades": "trades",
    "trade": "trades",
    "fills": "trades",
    "成交": "trades",
    "funds": "funds",
    "fund": "funds",
    "capital": "funds",
    "资金": "funds",
    "资产": "funds",
    "positions": "positions",
    "position": "positions",
    "holdings": "positions",
    "持仓": "positions",
}


def canonical_ui_panel(raw: str) -> Optional[str]:
    """将前端/人工输入的面板名规范为 orders|trades|funds|positions。"""
    s = (raw or "").strip()
    if not s:
        return None
    key = s.lower() if s.isascii() else s
    return _CANON_UI_PANEL_ALIASES.get(key) or _CANON_UI_PANEL_ALIASES.get(s)


def _unwrap_signal_payload(data: Any) -> Dict[str, Any]:
    """解密体偶尔被多包一层 payload/data；展开为扁平字段字典。"""
    if not isinstance(data, dict):
        return {}
    if any(
        k in data
        for k in ("signal_type", "signal_profile", "stock_code", "ui_panel", "panel", "volume")
    ):
        return data
    for key in ("payload", "data", "body", "inner", "order"):
        inner = data.get(key)
        if isinstance(inner, dict):
            return inner
    return data


def normalize_signal(data: Dict[str, Any]) -> Dict[str, Any]:
    """标准化并校验信号字段（含数量模式：绝对股数 / 目标仓位百分比）。"""
    data = dict(_unwrap_signal_payload(data))
    stock_code = str(data.get("stock_code", "") or "").strip().upper()
    raw_type = data.get("signal_type", data.get("signal_profile"))
    signal_type = str(raw_type or "").strip().upper()
    if not signal_type:
        p_in = str(data.get("ui_panel") or data.get("panel") or "").strip()
        if canonical_ui_panel(p_in):
            signal_type = "UI_READ"
    action_raw = data.get("action")
    action = str(action_raw).strip().upper() if action_raw not in (None, "", "NULL") else None
    price_type = str(data.get("price_type", "MARKET")).strip().upper()
    quantity_mode = str(data.get("quantity_mode") or "ABSOLUTE").strip().upper()

    if signal_type == "UI_READ":
        panel_raw = str(data.get("ui_panel") or data.get("panel") or "").strip()
        ui_panel = canonical_ui_panel(panel_raw)
        if not ui_panel:
            raise SignalValidationError(
                "UI_READ 须指定 ui_panel（或 panel），取值：orders|trades|funds|positions；"
                "也可用中文：委托、成交、资金、持仓"
            )
        if not stock_code:
            stock_code = "000000.SZ"
        if not _STOCK_RE.match(stock_code):
            raise SignalValidationError("stock_code 格式错误；UI_READ 可省略，默认 000000.SZ")
        return {
            "stock_code": stock_code,
            "signal_type": "UI_READ",
            "action": None,
            "volume": 0,
            "quantity_mode": "ABSOLUTE",
            "weight_pct": None,
            "price_type": "MARKET",
            "limit_price": None,
            "ui_panel": ui_panel,
        }

    if not _STOCK_RE.match(stock_code):
        raise SignalValidationError("stock_code 格式错误，示例：600519.SH")

    if signal_type not in ("TARGET", "ORDER"):
        raise SignalValidationError("signal_type 必须为 TARGET、ORDER 或 UI_READ")

    if price_type not in ("MARKET", "LIMIT"):
        raise SignalValidationError("price_type 必须为 MARKET 或 LIMIT")

    if quantity_mode not in ("ABSOLUTE", "TARGET_PCT"):
        raise SignalValidationError("quantity_mode 必须为 ABSOLUTE 或 TARGET_PCT")

    limit_price: Optional[float] = None
    if price_type == "LIMIT":
        raw_limit = data.get("limit_price")
        if raw_limit in (None, ""):
            raise SignalValidationError("price_type=LIMIT 时必须传 limit_price")
        try:
            limit_price = round(float(raw_limit), 3)
        except Exception as exc:
            raise SignalValidationError("limit_price 必须为数字") from exc
        if limit_price <= 0:
            raise SignalValidationError("limit_price 必须大于 0")

    weight_pct: Optional[float] = None
    volume = 0

    if quantity_mode == "ABSOLUTE":
        try:
            volume = int(data.get("volume"))
        except Exception as exc:
            raise SignalValidationError("quantity_mode=ABSOLUTE 时 volume 必须为整数") from exc
        if volume <= 0:
            raise SignalValidationError("volume 必须大于 0")
    else:
        raw_w = data.get("weight_pct")
        if raw_w in (None, ""):
            raise SignalValidationError("quantity_mode=TARGET_PCT 时必须传 weight_pct（如 25 表示 25%）")
        try:
            weight_pct = round(float(raw_w), 6)
        except Exception as exc:
            raise SignalValidationError("weight_pct 必须为数字") from exc
        if weight_pct <= 0 or weight_pct > 100:
            raise SignalValidationError("weight_pct 须在 (0, 100] 内")

    if signal_type == "ORDER":
        if action not in ("BUY", "SELL"):
            raise SignalValidationError("ORDER 模式下 action 必须为 BUY 或 SELL")
    else:
        # TARGET：按目标股数差调仓时 action 置空由执行器算方向；按目标仓位% 时须显式 BUY/SELL
        if quantity_mode == "TARGET_PCT":
            if action not in ("BUY", "SELL"):
                raise SignalValidationError(
                    "TARGET 且 quantity_mode=TARGET_PCT 时必须指定 action 为 BUY 或 SELL（与柜台「买/卖」方向一致）"
                )
        else:
            action = None

    return {
        "stock_code": stock_code,
        "signal_type": signal_type,
        "action": action,
        "volume": volume,
        "quantity_mode": quantity_mode,
        "weight_pct": weight_pct,
        "price_type": price_type,
        "limit_price": limit_price,
        "ui_panel": None,
    }


def insert_signal(repo: SignalRepository, raw_data: Dict[str, Any]) -> int:
    """对原始数据做标准化后写入数据库，返回 signal_id"""
    normalized = normalize_signal(raw_data)
    return repo.insert_signal(
        stock_code=normalized["stock_code"],
        signal_type=normalized["signal_type"],
        action=normalized["action"],
        volume=normalized["volume"],
        price_type=normalized["price_type"],
        limit_price=normalized["limit_price"],
        quantity_mode=normalized["quantity_mode"],
        weight_pct=normalized["weight_pct"],
        ui_panel=normalized.get("ui_panel"),
    )
