# -*- coding: utf-8 -*-
"""数据库迁移脚本：用于开发/上线前一键补齐表结构"""

import os
import sys

# 注入项目根目录，解决找不到 src 模块的问题
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from sqlalchemy import create_engine, text
from src.config import DB_CONFIG

def create_db_engine():
    db_url = (
        f"mysql+pymysql://{DB_CONFIG['user']}:{DB_CONFIG['password']}"
        f"@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}?charset={DB_CONFIG['charset']}"
    )
    return create_engine(db_url, pool_pre_ping=True, future=True)

def ensure_limit_price_column(engine):
    check_sql = text(
        """
        SELECT COUNT(*) AS cnt
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = :db
          AND TABLE_NAME = 'trade_signals'
          AND COLUMN_NAME = 'limit_price'
        """
    )

    with engine.begin() as conn:
        cnt = conn.execute(check_sql, {"db": DB_CONFIG["database"]}).scalar_one()
        if int(cnt) > 0:
            print("[MIGRATION] limit_price 已存在，跳过")
            return

        alter_sql = text(
            """
            ALTER TABLE `trade_signals`
            ADD COLUMN `limit_price` DECIMAL(10,3) NULL COMMENT '限价委托价格，MARKET可为空' AFTER `price_type`
            """
        )
        conn.execute(alter_sql)
        print("[MIGRATION] 已添加 trade_signals.limit_price")

def main():
    engine = create_db_engine()
    ensure_limit_price_column(engine)
    print("[MIGRATION] 完成")

if __name__ == "__main__":
    main()