# -*- coding: utf-8 -*-
"""RPA 专用异常：与业务可恢复错误区分，触发钉钉 + 截图留证。"""


class CriticalRpaError(RuntimeError):
    """AI 校验不一致、关键控件缺失、或防呆逻辑判定不可继续下单。"""

    def __init__(self, message: str, shot_path=None):
        super().__init__(message)
        self.shot_path = shot_path
