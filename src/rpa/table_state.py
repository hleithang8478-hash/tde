# -*- coding: utf-8 -*-
"""
委托 / 持仓表格解析后的纯函数：状态映射、代码与方向归一化、行匹配。

与具体截图/VLM 解耦，便于单测与复用。
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional, Tuple


# 常见 PTrade / 券商客户端「委托状态」→ EMS query_order 状态
CN_STATUS_TO_EMS: Dict[str, str] = {
    "已成": "FILLED",
    "全部成交": "FILLED",
    "已成交": "FILLED",
    "部成": "PARTIAL",
    "部分成交": "PARTIAL",
    "部撤": "PARTIAL",
    "已撤": "CANCELLED",
    "已撤销": "CANCELLED",
    "撤单": "CANCELLED",
    "废单": "REJECTED",
    "拒绝": "REJECTED",
    "拒单": "REJECTED",
    "失败": "REJECTED",
    "待报": "PENDING",
    "已报": "PENDING",
    "正报": "PENDING",
    "未报": "PENDING",
    "待成交": "PENDING",
    "已确认": "PENDING",
    "": "PENDING",
}


def normalize_code(v: Any) -> str:
    s = re.sub(r"\D", "", str(v or ""))
    if len(s) >= 6:
        return s[-6:].zfill(6)
    return s.zfill(6) if s else ""


def side_cn_to_action(side: Any) -> str:
    t = str(side or "").strip()
    if "卖" in t:
        return "SELL"
    if "买" in t:
        return "BUY"
    return ""


def map_cn_status(status_cn: Any) -> str:
    s = str(status_cn or "").strip()
    return CN_STATUS_TO_EMS.get(s, "PENDING")


def _int_field(v: Any) -> int:
    try:
        return int(float(str(v).replace(",", "").strip() or "0"))
    except Exception:
        return 0


def normalize_vlm_row(raw: Dict[str, Any]) -> Dict[str, Any]:
    """将 VLM 返回的一行压成统一字段名。"""
    code = normalize_code(
        raw.get("code")
        or raw.get("stock_code")
        or raw.get("证券代码")
        or raw.get("symbol")
    )
    side_raw = raw.get("side") or raw.get("direction") or raw.get("买卖方向") or ""
    action = side_cn_to_action(side_raw)
    st = map_cn_status(raw.get("status") or raw.get("委托状态") or "")
    ov = _int_field(raw.get("order_volume") or raw.get("volume") or raw.get("委托数量"))
    fv = _int_field(raw.get("filled_volume") or raw.get("成交数量"))
    uv = raw.get("unfilled_volume")
    if uv is None or str(uv).strip() == "":
        uv = max(0, ov - fv)
    else:
        uv = _int_field(uv)
    eid = str(raw.get("entrust_id") or raw.get("order_id") or raw.get("委托编号") or "").strip()
    return {
        "entrust_id": eid,
        "code": code,
        "action": action,
        "status": st,
        "order_volume": ov,
        "filled_volume": fv,
        "unfilled_volume": int(uv),
        "raw_status_cn": str(raw.get("status") or raw.get("委托状态") or ""),
    }


def normalize_vlm_rows(rows: Any) -> List[Dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    out: List[Dict[str, Any]] = []
    for r in rows:
        if isinstance(r, dict):
            out.append(normalize_vlm_row(r))
    return out


def is_cancellable_status_cn(raw_cn: str) -> bool:
    """结合中文状态判断是否仍可能允许撤单（启发式，以柜台为准）。"""
    s = str(raw_cn or "").strip()
    if not s:
        return True
    if any(x in s for x in ("已成", "已撤", "废单", "拒", "失败", "全成")):
        return False
    return True


def pick_row_for_our_order(
    rows: List[Dict[str, Any]],
    *,
    code: str,
    action: str,
    volume: int,
    broker_entrust_id: str = "",
) -> Optional[Dict[str, Any]]:
    """
    在表格行中选出最可能对应本笔 EMS 委托的一行。

    优先 entrust_id 精确匹配；否则按 code + BUY/SELL + 委托量 匹配，
    多条时优先「状态仍可撤」（启发式）。
    """
    c6 = normalize_code(code)
    act = action.upper().strip()
    want_vol = int(volume)

    if broker_entrust_id:
        for r in rows:
            if r.get("entrust_id") == broker_entrust_id:
                return r

    candidates: List[Dict[str, Any]] = []
    for r in rows:
        if r.get("code") != c6:
            continue
        if r.get("action") != act:
            continue
        ov = int(r.get("order_volume") or 0)
        if ov != want_vol:
            continue
        candidates.append(r)

    if not candidates:
        return None

    # 优先仍可撤的；否则取第一条
    def score(r: Dict[str, Any]) -> Tuple[int, str]:
        raw = r.get("raw_status_cn") or ""
        can = 1 if is_cancellable_status_cn(raw) else 0
        eid = r.get("entrust_id") or ""
        return (can, eid)

    candidates.sort(key=score, reverse=True)
    return candidates[0]


def order_age_seconds(placed_monotonic: float) -> float:
    return max(0.0, time.monotonic() - float(placed_monotonic or 0.0))
