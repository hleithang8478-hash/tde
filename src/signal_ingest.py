# -*- coding: utf-8 -*-
"""信号接入层：负责统一解析并写入 trade_signals"""

import re
from typing import Dict, Any, Optional

from src.core.repository import SignalRepository


class SignalValidationError(ValueError):
    """信号格式错误"""


_STOCK_RE = re.compile(r"^\d{6}\.(SH|SZ)$", re.IGNORECASE)


def normalize_signal(data: Dict[str, Any]) -> Dict[str, Any]:
    """标准化并校验信号字段"""
    stock_code = str(data.get("stock_code", "")).strip().upper()
    signal_type = str(data.get("signal_type", "")).strip().upper()
    action_raw = data.get("action")
    action = str(action_raw).strip().upper() if action_raw not in (None, "", "NULL") else None
    price_type = str(data.get("price_type", "MARKET")).strip().upper()

    if not _STOCK_RE.match(stock_code):
        raise SignalValidationError("stock_code 格式错误，示例：600519.SH")

    if signal_type not in ("TARGET", "ORDER"):
        raise SignalValidationError("signal_type 必须为 TARGET 或 ORDER")

    if price_type not in ("MARKET", "LIMIT"):
        raise SignalValidationError("price_type 必须为 MARKET 或 LIMIT")

    try:
        volume = int(data.get("volume"))
    except Exception as exc:
        raise SignalValidationError("volume 必须为整数") from exc

    if volume <= 0:
        raise SignalValidationError("volume 必须大于 0")

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

    if signal_type == "ORDER":
        if action not in ("BUY", "SELL"):
            raise SignalValidationError("ORDER 模式下 action 必须为 BUY 或 SELL")
    else:
        action = None

    return {
        "stock_code": stock_code,
        "signal_type": signal_type,
        "action": action,
        "volume": volume,
        "price_type": price_type,
        "limit_price": limit_price,
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
    )
