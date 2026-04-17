# -*- coding: utf-8 -*-
"""数据库访问层"""

from typing import Dict, Any, Optional, List
from sqlalchemy import text
from sqlalchemy.engine import Engine

from src.config import BATCH_SIZE


class SignalRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    def fetch_pending_signals(self, batch_size: int = BATCH_SIZE) -> List[Dict[str, Any]]:
        """查询待处理信号（按创建时间升序）"""
        sql = text("""
            SELECT signal_id, stock_code, signal_type, ui_panel, action, volume, quantity_mode, weight_pct,
                   price_type, limit_price, status, retry_count, create_time, update_time
            FROM trade_signals
            WHERE status = 'PENDING'
            ORDER BY create_time ASC
            LIMIT :limit_n
        """)
        with self.engine.connect() as conn:
            rows = conn.execute(sql, {"limit_n": batch_size}).mappings().all()
            return [dict(r) for r in rows]

    def claim_signal(self, signal_id: int) -> bool:
        """抢占信号处理权，避免重复执行"""
        sql = text("""
            UPDATE trade_signals
            SET status = 'PROCESSING', update_time = NOW()
            WHERE signal_id = :sid AND status = 'PENDING'
        """)
        with self.engine.begin() as conn:
            result = conn.execute(sql, {"sid": signal_id})
            return result.rowcount == 1

    def insert_signal(
        self,
        stock_code: str,
        signal_type: str,
        volume: int,
        price_type: str = "MARKET",
        action: Optional[str] = None,
        limit_price: Optional[float] = None,
        quantity_mode: str = "ABSOLUTE",
        weight_pct: Optional[float] = None,
        ui_panel: Optional[str] = None,
    ) -> int:
        """插入待执行信号，并返回 signal_id"""
        sql = text("""
            INSERT INTO trade_signals (
                stock_code,
                signal_type,
                ui_panel,
                action,
                volume,
                quantity_mode,
                weight_pct,
                price_type,
                limit_price,
                status,
                retry_count,
                create_time,
                update_time
            ) VALUES (
                :stock_code,
                :signal_type,
                :ui_panel,
                :action,
                :volume,
                :quantity_mode,
                :weight_pct,
                :price_type,
                :limit_price,
                'PENDING',
                0,
                NOW(),
                NOW()
            )
        """)
        with self.engine.begin() as conn:
            result = conn.execute(
                sql,
                {
                    "stock_code": stock_code,
                    "signal_type": signal_type,
                    "ui_panel": ui_panel,
                    "action": action,
                    "volume": volume,
                    "quantity_mode": quantity_mode,
                    "weight_pct": weight_pct,
                    "price_type": price_type,
                    "limit_price": limit_price,
                },
            )
            return int(result.lastrowid)

    def update_signal_status(
        self,
        signal_id: int,
        status: str,
        retry_count: Optional[int] = None,
        last_order_id: Optional[str] = None,
        last_error: Optional[str] = None
    ):
        """更新信号状态及附加信息"""
        sql = text("""
            UPDATE trade_signals
            SET status = :status,
                retry_count = COALESCE(:retry_count, retry_count),
                last_order_id = COALESCE(:last_order_id, last_order_id),
                last_error = :last_error,
                update_time = NOW()
            WHERE signal_id = :sid
        """)
        with self.engine.begin() as conn:
            conn.execute(sql, {
                "sid": signal_id,
                "status": status,
                "retry_count": retry_count,
                "last_order_id": last_order_id,
                "last_error": last_error
            })

    def complete_ui_read_success(
        self,
        signal_id: int,
        ui_ocr_text: str,
        retry_count: int,
    ) -> None:
        """UI_READ 成功：写入 OCR 全文并置 SUCCESS。"""
        sql = text("""
            UPDATE trade_signals
            SET status = 'SUCCESS',
                ui_ocr_text = :ocr,
                last_error = NULL,
                retry_count = :retry_count,
                update_time = NOW()
            WHERE signal_id = :sid
        """)
        with self.engine.begin() as conn:
            conn.execute(
                sql,
                {
                    "sid": signal_id,
                    "ocr": ui_ocr_text or "",
                    "retry_count": retry_count,
                },
            )
