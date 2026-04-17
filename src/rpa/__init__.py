# -*- coding: utf-8 -*-
"""Windows 客户端 RPA 交易执行子系统（替代 Ptrade 桥接）。"""

from src.rpa.exceptions import CriticalRpaError
from src.rpa.window_controller import WindowController
from src.rpa.vision_inspector import AiVisionInspector
from src.rpa.rpa_input_engine import RpaInputEngine
from src.rpa import table_state
from src.rpa import template_registry
from src.rpa.rpa_config_manager import RpaConfigManager

__all__ = [
    "CriticalRpaError",
    "WindowController",
    "AiVisionInspector",
    "RpaInputEngine",
    "table_state",
    "template_registry",
    "RpaConfigManager",
]
