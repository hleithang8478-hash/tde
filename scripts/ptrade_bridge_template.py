# -*- coding: utf-8 -*-
"""Ptrade 客户端内桥接模板（复制到 Ptrade 策略编辑器运行）"""

import sys
import threading

# Windows 服务器上请与部署目录一致（常见为 C:\软件\trader）；本机开发请按实际路径修改
PROJECT_ROOT = r"C:\软件\trader"
if PROJECT_ROOT not in sys.path:
    sys.path.append(PROJECT_ROOT)

from src.main import main


def initialize(context):
    log.info("正在启动云端 EMS 执行引擎...")
    ems_thread = threading.Thread(target=main)
    ems_thread.daemon = True
    ems_thread.start()
    log.info("EMS 引擎已在后台成功运行，正在监听数据库信号！")


def handle_data(context, data):
    # 保持空实现即可
    pass
