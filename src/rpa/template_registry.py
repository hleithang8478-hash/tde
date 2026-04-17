# -*- coding: utf-8 -*-
"""OpenCV 模板资源路径与标定注册表（由 scripts/rpa_calibrate.py 维护）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
REGISTRY_PATH = ASSETS_DIR / "registry.json"


def ensure_assets_dir() -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)


def template_png_path(template_name: str) -> Path:
    """``{template_name}.png``，位于 ``src/rpa/assets/``。"""
    safe = "".join(c for c in str(template_name).strip() if c.isalnum() or c in "_-")
    return ASSETS_DIR / f"{safe}.png"


def load_registry() -> Dict[str, Any]:
    ensure_assets_dir()
    if not REGISTRY_PATH.is_file():
        return {}
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_registry_merge(template_name: str, meta: Dict[str, Any]) -> None:
    """合并写入单条模板元数据（相对主窗的裁剪矩形）。"""
    ensure_assets_dir()
    reg = load_registry()
    reg[str(template_name).strip()] = dict(meta)
    REGISTRY_PATH.write_text(
        json.dumps(reg, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
