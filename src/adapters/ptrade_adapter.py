# -*- coding: utf-8 -*-
"""Ptrade接口适配层（真实执行代码）"""

from typing import Dict, Any

# --- Ptrade Built-in Stubs (For local linting) ---
try:
    get_position
except NameError:
    def get_position(sec): return None
    def order(sec, amount, limit_price=None): return None
    def get_order(order_id): return None
    def get_orders(): return {}
    def cancel_order(order_id): pass

    def log(): pass
# -------------------------------------------------


class PtradeAdapter:
    def __init__(self):
        # 在 Ptrade 客户端内运行时，get_position, order, get_order/get_orders 等函数是全局内置的。
        pass

    @staticmethod
    def _normalize_code(stock_code: str) -> str:
        """
        将常见代码尾缀标准化为 Ptrade 常见格式：
        - 600519.SH -> 600519.SS
        - 000001.SZ -> 000001.SZ
        """
        code = (stock_code or "").strip().upper()
        if code.endswith(".SH"):
            return code[:-3] + ".SS"
        return code

    @staticmethod
    def _to_int(value, default: int = 0) -> int:
        try:
            return int(float(value))
        except Exception:
            return default

    def get_position_volume(self, stock_code: str) -> int:
        """返回当前可用持仓股数（TARGET模式需要）"""
        sec = self._normalize_code(stock_code)
        try:
            pos = get_position(sec)
            if pos:
                # available_amount 表示可用数量（T+1冻结不计入）
                return self._to_int(getattr(pos, "available_amount", 0), 0)
            return 0
        except Exception as e:
            print(f"[PtradeAdapter] 获取持仓失败 {sec}: {e}")
            return 0

    def place_order(
        self,
        stock_code: str,
        action: str,
        volume: int,
        price_type: str,
        limit_price: float = None,
    ) -> str:
        """下单并返回委托单号 order_id"""
        sec = self._normalize_code(stock_code)
        amount = volume if action == "BUY" else -volume
        ptype = (price_type or "MARKET").upper()

        try:
            if ptype == "LIMIT":
                if limit_price is None or float(limit_price) <= 0:
                    raise ValueError("LIMIT 委托必须提供有效 limit_price")
                order_obj = order(sec, amount, limit_price=float(limit_price))
            else:
                order_obj = order(sec, amount)

            if order_obj:
                oid = getattr(order_obj, "order_id", "")
                if oid:
                    return str(oid)
            raise RuntimeError("Ptrade 返回空订单对象或无 order_id，可能被风控/资金拦截")
        except Exception as e:
            raise RuntimeError(f"调用 Ptrade 下单接口异常: {e}")

    def query_order(self, order_id: str) -> Dict[str, Any]:
        """查询委托状态并映射为系统标准状态"""
        ord_info = None

        # 优先 get_order(order_id)
        try:
            ord_info = get_order(order_id)
        except Exception:
            ord_info = None

        # 兼容 get_orders(...) 返回字典/列表等
        if not ord_info:
            try:
                all_orders = get_orders()
                if isinstance(all_orders, dict):
                    ord_info = all_orders.get(order_id)
            except Exception:
                ord_info = None

        if not ord_info:
            raise ValueError(f"在 Ptrade 找不到订单 {order_id}")

        status_code = self._to_int(getattr(ord_info, "status", -1), -1)

        # 不同柜台字段可能不同，做兼容兜底
        filled = self._to_int(
            getattr(ord_info, "filled", None)
            if hasattr(ord_info, "filled")
            else getattr(ord_info, "business_amount", 0),
            0,
        )
        total_amount = self._to_int(getattr(ord_info, "amount", 0), 0)
        unfilled = max(abs(total_amount) - abs(filled), 0)

        # 状态映射：0未报,1待报,2已报,3报入,4废单,5部成,6已成,7部撤,8已撤,9待撤
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
        """撤销未成交部分"""
        try:
            cancel_order(order_id)
            return True
        except Exception as e:
            print(f"[PtradeAdapter] 撤单失败 order_id={order_id}: {e}")
            return False
