# -*- coding: utf-8 -*-
"""主循环入口"""

import time
import traceback
from datetime import datetime

from sqlalchemy import create_engine

from src.config import DB_CONFIG, BATCH_SIZE, POLL_INTERVAL_SECONDS
from src.notifier.system_notifier import send_notification
from src.adapters.ptrade_adapter import PtradeAdapter
from src.core.repository import SignalRepository
from src.core.executor import TradeExecutor


def create_db_engine():
    """SQLAlchemy连接池"""
    db_url = (
        f"mysql+pymysql://{DB_CONFIG['user']}:{DB_CONFIG['password']}"
        f"@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}?charset={DB_CONFIG['charset']}"
    )
    return create_engine(
        db_url,
        pool_size=5,
        max_overflow=10,
        pool_recycle=1800,
        pool_pre_ping=True,
        future=True,
    )


def main():
    engine = create_db_engine()
    repo = SignalRepository(engine)
    broker = PtradeAdapter()
    executor = TradeExecutor(repo, broker)

    print(f"[{datetime.now()}] EMS启动成功，开始轮询...")

    while True:
        try:
            signals = repo.fetch_pending_signals(BATCH_SIZE)
            if signals:
                for sig in signals:
                    sid = sig["signal_id"]
                    try:
                        claimed = repo.claim_signal(sid)
                        if not claimed:
                            continue
                        executor.process_signal(sig)
                    except Exception:
                        print(f"[ERROR] 信号处理异常 sid={sid}")
                        print(traceback.format_exc())
            time.sleep(POLL_INTERVAL_SECONDS)

        except Exception as e:
            err = f"{type(e).__name__}: {str(e)}"
            print(f"[FATAL LOOP ERROR] {err}")
            print(traceback.format_exc())
            send_notification("EMS系统通知", f"[系统异常]\nEMS主轮询异常: {err}\n已自动进入下一轮重试")
            time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
