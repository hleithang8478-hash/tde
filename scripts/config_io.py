# -*- coding: utf-8 -*-
"""读写 src/config.py（整文件重写，供 merge_legacy / simple_rpa_wizard 使用）。"""

from __future__ import annotations

import copy
import importlib.util
import pprint
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Optional


def _pfmt(obj: Any) -> str:
    return pprint.pformat(obj, width=108, sort_dicts=False)


def load_config_module(path: Path, name: str = "_ems_cfg_mod") -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def render_config_py_text(
    *,
    deploy: Dict[str, Any],
    db: Dict[str, Any],
    notify: str,
    smtp: Dict[str, Any],
    poll: int,
    max_retry: int,
    batch: int,
    order_wait: int,
    order_poll: int,
    exec_only: bool,
    email_ingest: Dict[str, Any],
    api: Dict[str, Any],
    rpa: Dict[str, Any],
) -> str:
    lines: list[str] = []
    ap = lines.append
    ap("# -*- coding: utf-8 -*-")
    ap('"""Global config; maintained by scripts (UTF-8)."""')
    ap("")
    ap("# DEPLOY_PATHS")
    ap(f"DEPLOY_PATHS = {_pfmt(deploy)}")
    ap("")
    ap(f"DB_CONFIG = {_pfmt(db)}")
    ap("")
    ap(f'NOTIFY_MODE = {repr(str(notify))}  # NONE or EMAIL')
    ap(f"SMTP_CONFIG = {_pfmt(smtp)}")
    ap("")
    ap(f"POLL_INTERVAL_SECONDS = {poll}")
    ap(f"MAX_RETRY = {max_retry}")
    ap(f"BATCH_SIZE = {batch}")
    ap("# ORDER_WAIT_TIMEOUT: ignored when EXECUTOR_SUBMIT_ONLY_MODE is True")
    ap(f"ORDER_WAIT_TIMEOUT = {order_wait}")
    ap(f"ORDER_POLL_INTERVAL = {order_poll}")
    ap("")
    ap("# True: after place_order mark SUCCESS, no query_order polling")
    ap(f"EXECUTOR_SUBMIT_ONLY_MODE = {exec_only}")
    ap("")
    ap(f"EMAIL_INGEST_CONFIG = {_pfmt(email_ingest)}")
    ap("")
    ap(f"API_CONFIG = {_pfmt(api)}")
    ap("")
    ap("# RPA_CONFIG (mouse flow: order_ui_mouse_flow; tabs_* kept for legacy Tab path only)")
    ap(f"RPA_CONFIG = {_pfmt(rpa)}")
    ap("")
    return "\n".join(lines) + "\n"


def write_project_config(
    project_root: Path,
    *,
    rpa: Dict[str, Any],
    exec_submit_only: Optional[bool] = None,
    backup: bool = False,
    dry_run: bool = False,
) -> Path:
    """用磁盘上当前 config 的非 RPA 段 + 传入的 rpa（完整 dict）重写 ``src/config.py``。"""
    cfg_path = project_root.resolve() / "src" / "config.py"
    if not cfg_path.is_file():
        raise FileNotFoundError(cfg_path)
    m = load_config_module(cfg_path, "_ems_cfg_write")

    deploy = dict(m.DEPLOY_PATHS)
    db = dict(m.DB_CONFIG)
    notify = str(m.NOTIFY_MODE)
    smtp = dict(m.SMTP_CONFIG)
    poll = int(m.POLL_INTERVAL_SECONDS)
    max_retry = int(m.MAX_RETRY)
    batch = int(m.BATCH_SIZE)
    order_wait = int(m.ORDER_WAIT_TIMEOUT)
    order_poll = int(m.ORDER_POLL_INTERVAL)
    ex = bool(getattr(m, "EXECUTOR_SUBMIT_ONLY_MODE", True)) if exec_submit_only is None else bool(exec_submit_only)
    email_ingest = dict(m.EMAIL_INGEST_CONFIG)
    api = dict(m.API_CONFIG)

    text = render_config_py_text(
        deploy=deploy,
        db=db,
        notify=notify,
        smtp=smtp,
        poll=poll,
        max_retry=max_retry,
        batch=batch,
        order_wait=order_wait,
        order_poll=order_poll,
        exec_only=ex,
        email_ingest=email_ingest,
        api=api,
        rpa=copy.deepcopy(rpa),
    )

    if dry_run:
        print(text[:6000])
        return cfg_path

    if backup and cfg_path.exists():
        bak = cfg_path.with_suffix(f".py.bak_{time.strftime('%Y%m%d_%H%M%S')}")
        shutil.copy2(cfg_path, bak)
        print(f"[备份] {bak}")

    cfg_path.write_text(text, encoding="utf-8")
    return cfg_path
