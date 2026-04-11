# -*- coding: utf-8 -*-
"""命令行录单：人工登录后手工输入订单并写入数据库"""

import os
import sys

from sqlalchemy import create_engine

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.config import DB_CONFIG
from src.core.repository import SignalRepository
from src.signal_ingest import insert_signal, SignalValidationError


def create_db_engine():
    db_url = (
        f"mysql+pymysql://{DB_CONFIG['user']}:{DB_CONFIG['password']}"
        f"@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}?charset={DB_CONFIG['charset']}"
    )
    return create_engine(db_url, pool_pre_ping=True, future=True)


def ask(prompt: str, default: str = "") -> str:
    if default:
        value = input(f"{prompt} [{default}]: ").strip()
        return value if value else default
    return input(f"{prompt}: ").strip()


def main():
    engine = create_db_engine()
    repo = SignalRepository(engine)

    print("=== EMS 手工录单 ===")
    print("支持 signal_type=ORDER/TARGET")

    while True:
        try:
            stock_code = ask("股票代码(如 600519.SH)")
            signal_type = ask("信号类型(ORDER/TARGET)", "ORDER").upper()
            action = ask("动作(BUY/SELL，TARGET可留空)", "BUY")
            volume = ask("数量(整数)")
            price_type = ask("价格类型(MARKET/LIMIT)", "MARKET").upper()

            raw = {
                "stock_code": stock_code,
                "signal_type": signal_type,
                "action": action,
                "volume": volume,
                "price_type": price_type,
            }
            signal_id = insert_signal(repo, raw)
            print(f"[OK] 已写入信号 signal_id={signal_id}")

        except SignalValidationError as e:
            print(f"[参数错误] {e}")
        except Exception as e:
            print(f"[系统错误] {type(e).__name__}: {e}")

        cont = ask("继续录入？(y/n)", "y").lower()
        if cont != "y":
            break


if __name__ == "__main__":
    main()
