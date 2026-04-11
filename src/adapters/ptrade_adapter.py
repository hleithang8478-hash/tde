# -*- coding: utf-8 -*-
"""Ptrade接口适配层（需按券商环境替换）"""

from typing import Dict, Any


class PtradeAdapter:
    """
    你需要将以下方法替换为山西证券 Ptrade 的实际调用：
    1) get_position_volume
    2) place_order
    3) query_order
    4) cancel_order
    """

    def get_position_volume(self, stock_code: str) -> int:
        """返回当前可用持仓股数（TARGET模式需要）"""
        # TODO: 替换为你的Ptrade持仓查询函数
        raise NotImplementedError("请替换 get_position_volume 为 Ptrade 实际API")

    def place_order(self, stock_code: str, action: str, volume: int, price_type: str) -> str:
        """
        下单并返回委托单号 order_id
        action: BUY / SELL
        price_type: MARKET / LIMIT
        """
        # TODO: 替换为你的Ptrade下单函数
        raise NotImplementedError("请替换 place_order 为 Ptrade 实际API")

    def query_order(self, order_id: str) -> Dict[str, Any]:
        """
        查询委托状态，统一返回：
        {
            "status": "FILLED" | "PARTIAL" | "PENDING" | "CANCELLED" | "REJECTED",
            "filled_volume": 100,
            "unfilled_volume": 900
        }
        """
        # TODO: 替换为你的Ptrade委托查询函数
        raise NotImplementedError("请替换 query_order 为 Ptrade 实际API")

    def cancel_order(self, order_id: str) -> bool:
        """撤销未成交部分，成功返回True"""
        # TODO: 替换为你的Ptrade撤单函数
        raise NotImplementedError("请替换 cancel_order 为 Ptrade 实际API")
