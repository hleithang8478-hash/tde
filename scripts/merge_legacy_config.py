#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将「旧版」src/config.py 中的配置合并进本工程当前的 src/config.py 结构。

- 以本工程当前 ``src/config.py`` 为模板（保留新键与默认值）。
- 仅覆盖旧文件里**已存在且同名**的键；旧版多出的未知键会忽略。
- ``opencv_templates`` / ``API_CONFIG['hmac_keys']`` 等子 dict 做浅层合并。

用法::

    python scripts/merge_legacy_config.py --legacy "D:\\\\old\\\\trader\\\\src\\\\config.py"
    python scripts/merge_legacy_config.py --legacy C:\\\\软件\\\\trader\\\\src\\\\config.py --project-root .
    python scripts/merge_legacy_config.py --legacy old.py --dry-run

合并后再用 ``scripts/simple_rpa_wizard.py`` 逐项补坐标，或 ``scripts/configure_interactive.py`` / 手改 ``src/config.py``。
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict


def _import_config_io() -> Any:
    p = Path(__file__).resolve().parent / "config_io.py"
    spec = importlib.util.spec_from_file_location("ems_config_io_merge", p)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"找不到 config_io: {p}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _configure_stdio_utf8() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass


def _merge_flat_dict(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """以 base 为准；仅当 overlay 的键在 base 中已存在时才覆盖（子 dict 浅合并）。"""
    out = copy.deepcopy(base)
    for k, v in overlay.items():
        if k not in out:
            continue
        if isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = {**out[k], **copy.deepcopy(v)}
        else:
            out[k] = copy.deepcopy(v)
    return out


def merge_legacy_into_project(
    legacy_path: Path,
    project_root: Path,
    *,
    backup: bool = True,
    dry_run: bool = False,
) -> Path:
    cio = _import_config_io()
    base_path = project_root.resolve() / "src" / "config.py"
    if not base_path.is_file():
        raise FileNotFoundError(f"本工程缺少模板配置: {base_path}")

    legacy_mod = cio.load_config_module(legacy_path.resolve(), "_ems_legacy_config")
    base_mod = cio.load_config_module(base_path, "_ems_base_config")

    deploy = _merge_flat_dict(dict(base_mod.DEPLOY_PATHS), dict(getattr(legacy_mod, "DEPLOY_PATHS", {})))
    db = _merge_flat_dict(dict(base_mod.DB_CONFIG), dict(getattr(legacy_mod, "DB_CONFIG", {})))

    notify = getattr(legacy_mod, "NOTIFY_MODE", base_mod.NOTIFY_MODE)
    smtp = _merge_flat_dict(dict(base_mod.SMTP_CONFIG), dict(getattr(legacy_mod, "SMTP_CONFIG", {})))

    poll = int(getattr(legacy_mod, "POLL_INTERVAL_SECONDS", base_mod.POLL_INTERVAL_SECONDS))
    max_retry = int(getattr(legacy_mod, "MAX_RETRY", base_mod.MAX_RETRY))
    batch = int(getattr(legacy_mod, "BATCH_SIZE", base_mod.BATCH_SIZE))
    order_wait = int(getattr(legacy_mod, "ORDER_WAIT_TIMEOUT", base_mod.ORDER_WAIT_TIMEOUT))
    order_poll = int(getattr(legacy_mod, "ORDER_POLL_INTERVAL", base_mod.ORDER_POLL_INTERVAL))
    exec_only = bool(getattr(legacy_mod, "EXECUTOR_SUBMIT_ONLY_MODE", getattr(base_mod, "EXECUTOR_SUBMIT_ONLY_MODE", True)))

    email_ingest = _merge_flat_dict(dict(base_mod.EMAIL_INGEST_CONFIG), dict(getattr(legacy_mod, "EMAIL_INGEST_CONFIG", {})))
    api = _merge_flat_dict(dict(base_mod.API_CONFIG), dict(getattr(legacy_mod, "API_CONFIG", {})))
    rpa = _merge_flat_dict(dict(base_mod.RPA_CONFIG), dict(getattr(legacy_mod, "RPA_CONFIG", {})))

    content = cio.render_config_py_text(
        deploy=deploy,
        db=db,
        notify=str(notify),
        smtp=smtp,
        poll=poll,
        max_retry=max_retry,
        batch=batch,
        order_wait=order_wait,
        order_poll=order_poll,
        exec_only=exec_only,
        email_ingest=email_ingest,
        api=api,
        rpa=rpa,
    )

    if dry_run:
        print(content[:4000])
        if len(content) > 4000:
            print("\n... [dry-run 截断，共 %d 字节] ..." % len(content.encode("utf-8")))
        return base_path

    if backup and base_path.exists():
        bak = base_path.with_suffix(f".py.bak_{time.strftime('%Y%m%d_%H%M%S')}")
        shutil.copy2(base_path, bak)
        print(f"[备份] {bak}")

    base_path.write_text(content, encoding="utf-8")
    print(f"[完成] 已写入: {base_path}")
    return base_path


def main() -> int:
    _configure_stdio_utf8()
    p = argparse.ArgumentParser(description="从旧 config.py 合并进本工程 src/config.py")
    p.add_argument("--legacy", type=Path, required=True, help="旧版 src/config.py 绝对或相对路径")
    p.add_argument("--project-root", type=Path, default=None, help="本工程根（默认：本脚本上级目录）")
    p.add_argument("--dry-run", action="store_true", help="只打印合并结果，不写文件")
    p.add_argument("--no-backup", action="store_true", help="写入前不备份当前 config.py")
    args = p.parse_args()

    root = (args.project_root or Path(__file__).resolve().parent.parent).resolve()
    try:
        merge_legacy_into_project(
            args.legacy,
            root,
            backup=not args.no_backup,
            dry_run=args.dry_run,
        )
    except Exception as e:
        print(f"[错误] {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
