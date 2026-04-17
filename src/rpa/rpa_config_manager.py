# -*- coding: utf-8 -*-
"""
RPA 配置加载：合并 ``src.config.RPA_CONFIG`` 与 ``src/rpa/assets/registry.json`` 中的 OpenCV 模板元数据。
"""

from __future__ import annotations

import copy
from typing import Any, Dict

from src.rpa import template_registry


class RpaConfigManager:
    """
    统一管理 RPA 相关配置的读取与合并，避免各模块重复实现 ``registry.json`` 合并逻辑。
    """

    @staticmethod
    def load_merged() -> Dict[str, Any]:
        """
        返回 ``RPA_CONFIG`` 的深拷贝，并将 ``assets/registry.json`` 中的条目
        合并进 ``opencv_templates``（同名字段以 registry 为准）。
        """
        from src.config import RPA_CONFIG

        base = RPA_CONFIG if isinstance(RPA_CONFIG, dict) else {}
        cfg: Dict[str, Any] = copy.deepcopy(base)
        reg = template_registry.load_registry()
        existing = cfg.get("opencv_templates")
        if not isinstance(existing, dict):
            existing = {}
        cfg["opencv_templates"] = {**existing, **reg}
        return cfg
