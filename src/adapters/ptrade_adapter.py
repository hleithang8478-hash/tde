# -*- coding: utf-8 -*-
"""Ptrade 接口适配层：对齐恒生投研 API 文档的下单、市价类型、价格精度与查单。"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from src.config import PTRADE_CONFIG

# --- Ptrade 内置函数占位（本地 lint / 非客户端环境）---
try:
    get_position
except NameError:
    def get_position(sec):  # type: ignore
        return None

    def order(security, amount, limit_price=None):  # type: ignore
        return None

    def order_market(security, amount, market_type, limit_price=None):  # type: ignore
        return None

    def get_order(order_id):  # type: ignore
        return None

    def get_orders():  # type: ignore
        return {}

    def get_all_orders():  # type: ignore
        return {}

    def get_open_orders():  # type: ignore
        return {}

    def get_snapshot(security):  # type: ignore
        return {}

    def cancel_order(order_id):  # type: ignore
        pass

    def cancel_order_ex(order_id):  # type: ignore
        pass

    def log(*args, **kwargs):  # type: ignore
        pass


# ---------------------------------------------------


def _cfg() -> Dict[str, Any]:
    return PTRADE_CONFIG if isinstance(PTRADE_CONFIG, dict) else {}


class PtradeAdapter:
    """在 Ptrade 策略进程内运行时，order/get_order 等为平台注入的全局函数。"""

    def __init__(self):
        pass

    @staticmethod
    def _normalize_code(stock_code: str) -> str:
        code = (stock_code or "").strip().upper()
        if code.endswith(".SH"):
            return code[:-3] + ".SS"
        return code

    @staticmethod
    def _to_int(value: Any, default: int = 0) -> int:
        try:
            return int(float(value))
        except Exception:
            return default

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return default

    @classmethod
    def _infer_price_decimals(cls, sec: str) -> int:
        """文档：股票常见 2 位小数；ETF/国债等 3 位。按代码前缀粗判。"""
        overrides: Dict[str, int] = _cfg().get("price_decimal_overrides") or {}
        if sec in overrides:
            return int(overrides[sec])
        base = sec.split(".")[0] if "." in sec else sec
        if not base.isdigit():
            return 3
        p3 = _cfg().get("three_decimal_prefixes") or (
            "51",
            "56",
            "58",
            "159",
            "16",
            "18",
            "511",
            "512",
            "513",
            "515",
            "516",
            "518",
        )
        for prefix in p3:
            if base.startswith(prefix):
                return 3
        return 2

    @classmethod
    def _quantize_price(cls, sec: str, price: float) -> float:
        dec = cls._infer_price_decimals(sec)
        q = Decimal("1").scaleb(-dec)
        d = Decimal(str(price)).quantize(q, rounding=ROUND_HALF_UP)
        return float(d)

    def _snapshot_row(self, sec: str) -> Optional[Dict[str, Any]]:
        try:
            snap = get_snapshot(sec)
        except Exception:
            return None
        if not snap or not isinstance(snap, dict):
            return None
        row = snap.get(sec)
        if row is None and len(snap) == 1:
            row = next(iter(snap.values()))
        if not isinstance(row, dict):
            return None
        return row

    def _protection_limit_price(self, sec: str, action: str) -> Optional[float]:
        """order_market 上证必填保护限价；深市场景下也可传入以增强兼容性。"""
        row = self._snapshot_row(sec)
        if not row:
            return None

        last = self._to_float(row.get("last_px"), 0.0)
        if last <= 0:
            last = self._to_float(row.get("preclose_px"), 0.0)
        if last <= 0:
            return None

        up_px = self._to_float(row.get("up_px"), 0.0)
        down_px = self._to_float(row.get("down_px"), 0.0)
        tick = self._to_float(row.get("tick_size"), 0.01) or 0.01

        buy_slip = float(_cfg().get("market_protect_buy_slippage", 0.02))
        sell_slip = float(_cfg().get("market_protect_sell_slippage", 0.02))

        if action == "BUY":
            raw = last * (1.0 + buy_slip)
            if up_px > 0:
                raw = min(raw, up_px)
            prot = max(raw, last + tick)
        else:
            raw = last * (1.0 - sell_slip)
            if down_px > 0:
                raw = max(raw, down_px)
            prot = raw

        return self._quantize_price(sec, prot)

    @staticmethod
    def _is_shanghai(sec: str) -> bool:
        return sec.endswith(".SS")

    def _place_market_order(self, sec: str, amount: int) -> Any:
        mode = str(_cfg().get("market_order_mode", "snapshot_order")).lower().strip()
        action = "BUY" if amount > 0 else "SELL"

        if mode != "order_market":
            return order(sec, amount)

        sse_mt = int(_cfg().get("sse_market_type", 0))
        sz_mt = int(_cfg().get("sz_market_type", 0))
        protect = self._protection_limit_price(sec, action)

        try:
            if self._is_shanghai(sec):
                if protect is None:
                    print(
                        f"[PtradeAdapter] order_market 上证需要保护限价，"
                        f"快照不可用，回退 order() 快照价委托: {sec}"
                    )
                    return order(sec, amount)
                return order_market(sec, amount, sse_mt, protect)
            # 深证：文档示例多数可不传 limit_price；若配置了保护价则传入
            if protect is not None and _cfg().get("sz_market_use_protect_limit", False):
                return order_market(sec, amount, sz_mt, protect)
            return order_market(sec, amount, sz_mt)
        except Exception as e:
            print(f"[PtradeAdapter] order_market 失败，回退 order(): {e}")
            return order(sec, amount)

    def place_order(
        self,
        stock_code: str,
        action: str,
        volume: int,
        price_type: str,
        limit_price: Optional[float] = None,
    ) -> str:
        sec = self._normalize_code(stock_code)
        amount = volume if action == "BUY" else -volume
        ptype = (price_type or "MARKET").upper()

        try:
            if ptype == "LIMIT":
                if limit_price is None or float(limit_price) <= 0:
                    raise ValueError("LIMIT 委托必须提供有效 limit_price")
                lp = self._quantize_price(sec, float(limit_price))
                order_obj = order(sec, amount, limit_price=lp)
            else:
                order_obj = self._place_market_order(sec, amount)

            oid = self._extract_order_id(order_obj)
            if oid:
                return oid
            raise RuntimeError("Ptrade 返回空订单对象或无订单编号，可能被风控/资金/股票池拦截")
        except Exception as e:
            raise RuntimeError(f"调用 Ptrade 下单接口异常: {e}") from e

    @staticmethod
    def _extract_order_id(order_obj: Any) -> str:
        if order_obj is None:
            return ""
        if isinstance(order_obj, str):
            return order_obj.strip()
        oid = getattr(order_obj, "order_id", None)
        if oid is None or str(oid).strip() == "":
            oid = getattr(order_obj, "id", None)
        if oid is None:
            return ""
        return str(oid).strip()

    def _resolve_order_record(self, order_id: str) -> Any:
        oid = str(order_id).strip()

        try:
            o = get_order(oid)
            if o is not None:
                return o
        except Exception:
            pass

        try:
            found = self._find_in_orders_container(get_orders(), oid)
            if found is not None:
                return found
        except Exception:
            pass

        try:
            found = self._find_in_orders_container(get_all_orders(), oid)
            if found is not None:
                return found
        except Exception:
            pass

        try:
            found = self._find_in_orders_container(get_open_orders(), oid)
            if found is not None:
                return found
        except Exception:
            pass

        raise ValueError(f"在 Ptrade 找不到订单 {oid}")

    @staticmethod
    def _find_in_orders_container(container: Any, oid: str) -> Any:
        if container is None:
            return None

        if isinstance(container, dict):
            if oid in container:
                return container[oid]
            for k, v in container.items():
                if str(k) == oid:
                    return v
                rid = PtradeAdapter._id_from_obj_or_dict(v)
                if rid and str(rid) == oid:
                    return v

        if isinstance(container, list):
            for item in container:
                rid = PtradeAdapter._id_from_obj_or_dict(item)
                if rid and str(rid) == oid:
                    return item

        return None

    @staticmethod
    def _id_from_obj_or_dict(item: Any) -> Optional[str]:
        if item is None:
            return None
        if isinstance(item, dict):
            for key in ("order_id", "id", "entrust_no"):
                v = item.get(key)
                if v is not None and str(v).strip() != "":
                    return str(v).strip()
            return None
        oid = getattr(item, "order_id", None)
        if oid is None:
            oid = getattr(item, "id", None)
        if oid is None:
            return None
        return str(oid).strip()

    def query_order(self, order_id: str) -> Dict[str, Any]:
        ord_info = self._resolve_order_record(order_id)

        if isinstance(ord_info, dict):
            raw_st = ord_info.get("status", -1)
        else:
            raw_st = getattr(ord_info, "status", -1)
        status_code = self._to_int(raw_st, -1)

        if isinstance(ord_info, dict):
            filled = self._to_int(
                ord_info.get("filled")
                if ord_info.get("filled") is not None
                else ord_info.get("business_amount", 0),
                0,
            )
            total_amount = self._to_int(ord_info.get("amount", 0), 0)
        else:
            filled = self._to_int(
                getattr(ord_info, "filled", None)
                if hasattr(ord_info, "filled")
                else getattr(ord_info, "business_amount", 0),
                0,
            )
            total_amount = self._to_int(getattr(ord_info, "amount", 0), 0)

        unfilled = max(abs(total_amount) - abs(filled), 0)

        # 状态：0未报,1待报,2已报,3报入,4废单,5部成,6已成,7部撤,8已撤,9待撤
        if status_code == 6:
            mapped_status = "FILLED"
        elif status_code == 4:
            mapped_status = "REJECTED"
        elif status_code in (7, 8):
            mapped_status = "CANCELLED"
        elif status_code == 5:
            mapped_status = "PARTIAL"
        else:
            mapped_status = "PENDING"

        return {
            "status": mapped_status,
            "filled_volume": abs(filled),
            "unfilled_volume": unfilled,
        }

    def cancel_order(self, order_id: str) -> bool:
        oid = str(order_id)
        try:
            cancel_order(oid)
            return True
        except Exception as e1:
            try:
                cancel_order_ex(oid)
                return True
            except Exception as e2:
                print(f"[PtradeAdapter] 撤单失败 order_id={oid}: {e1}; cancel_order_ex: {e2}")
                return False

    def get_position_volume(self, stock_code: str) -> int:
        sec = self._normalize_code(stock_code)
        try:
            pos = get_position(sec)
            if pos:
                return self._to_int(getattr(pos, "available_amount", 0), 0)
            return 0
        except Exception as e:
            print(f"[PtradeAdapter] 获取持仓失败 {sec}: {e}")
            return 0
