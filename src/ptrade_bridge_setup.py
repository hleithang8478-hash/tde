# -*- coding: utf-8 -*-
"""Ptrade 策略 initialize 阶段：构建 set_universe 股票池（在客户端内运行）。"""

from __future__ import annotations

from typing import List

from src.config import DB_CONFIG, PTRADE_CONFIG


def normalize_ptrade_code(stock_code: str) -> str:
    code = (stock_code or "").strip().upper()
    if code.endswith(".SH"):
        return code[:-3] + ".SS"
    return code


def _db_distinct_stock_codes() -> List[str]:
    try:
        from sqlalchemy import create_engine, text
    except Exception as e:
        print(f"[EMS-Bridge] 跳过库内标的合并（sqlalchemy 不可用）: {e}")
        return []

    url = (
        f"mysql+pymysql://{DB_CONFIG['user']}:{DB_CONFIG['password']}"
        f"@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}"
        f"?charset={DB_CONFIG['charset']}"
    )
    try:
        engine = create_engine(url, pool_pre_ping=True, future=True)
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT DISTINCT stock_code FROM trade_signals "
                    "WHERE stock_code IS NOT NULL AND TRIM(stock_code) <> ''"
                )
            ).fetchall()
        return [str(r[0]).strip() for r in rows if r[0]]
    except Exception as e:
        print(f"[EMS-Bridge] 读取 trade_signals 标的失败（可仅用静态池）: {e}")
        return []


def collect_universe_codes() -> List[str]:
    """合并配置静态池 + 可选库内出现过的代码，去重。"""
    static = PTRADE_CONFIG.get("universe_static") or []
    include_db = bool(PTRADE_CONFIG.get("universe_include_db_distinct", True))

    out: List[str] = []
    for x in static:
        n = normalize_ptrade_code(str(x).strip())
        if n:
            out.append(n)
    if include_db:
        for c in _db_distinct_stock_codes():
            out.append(normalize_ptrade_code(c))

    seen = set()
    uniq: List[str] = []
    for c in out:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq
