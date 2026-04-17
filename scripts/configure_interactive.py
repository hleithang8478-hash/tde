#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交互配置：中文提问，生成英文键名的 ``src/config.py``。

- 可随时重新运行本脚本，会覆盖 ``src/config.py``（不会删 ``src/rpa/assets`` 里模板图）。
- 三档：快速 / 标准 / 完整。不懂 RPA 坐标时选「快速」或标准里选「暂不配坐标」。

用法::

    python scripts/configure_interactive.py
    python scripts/configure_interactive.py --project-root D:\\\\app\\\\trader
    python scripts/configure_interactive.py --mode quick

从旧机迁移配置（先合并再跑向导补坐标）::

    python scripts/merge_legacy_config.py --legacy "C:\\\\old\\\\trader\\\\src\\\\config.py"
    python scripts/configure_interactive.py --mode full
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import List, Optional, Tuple


def _configure_stdio_utf8() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    if hasattr(sys.stdin, "reconfigure"):
        try:
            sys.stdin.reconfigure(encoding="utf-8")
        except Exception:
            pass


def ask(提示: str, 默认值: str = "") -> str:
    if 默认值:
        raw = input(f"{提示} [默认: {默认值}]: ").strip()
        return raw if raw else 默认值
    return input(f"{提示}: ").strip()


def ask_bool(提示: str, 默认否: bool = True) -> bool:
    d = "n" if 默认否 else "y"
    v = ask(f"{提示}（y=是 / n=否）", d).lower()
    return v in ("y", "yes", "true", "1", "是")


def py_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def split_csv_tokens(s: str) -> List[str]:
    return [x.strip() for x in re.split(r"[,，;；\s]+", s) if x.strip()]


def parse_bbox(行: str) -> Optional[Tuple[int, int, int, int]]:
    行 = 行.strip()
    if not 行 or 行.lower() in ("none", "无", "null"):
        return None
    行 = 行.replace("，", ",")
    parts = [int(x.strip()) for x in 行.split(",")]
    if len(parts) != 4:
        raise ValueError("需要恰好 4 个整数：左, 上, 宽度, 高度（英文逗号分隔）")
    return (parts[0], parts[1], parts[2], parts[3])


def parse_xy(行: str) -> Optional[Tuple[int, int]]:
    行 = 行.strip()
    if not 行 or 行.lower() in ("none", "无", "null"):
        return None
    行 = 行.replace("，", ",")
    parts = [int(x.strip()) for x in 行.split(",")]
    if len(parts) != 2:
        raise ValueError("需要两个整数：x, y（英文逗号分隔）")
    return (parts[0], parts[1])


def fmt_tuple_int4(t: Optional[Tuple[int, int, int, int]]) -> str:
    if t is None:
        return "None"
    return f"({t[0]}, {t[1]}, {t[2]}, {t[3]})"


def fmt_tuple_int2(t: Optional[Tuple[int, int]]) -> str:
    if t is None:
        return "None"
    return f"({t[0]}, {t[1]})"


def print_block(title: str, body: str) -> None:
    print("\n" + "─" * 48)
    print(f"  {title}")
    print("─" * 48)
    print(body.strip())


# ---------- 各参数「去哪找」说明（只打在屏幕上，不进 config）----------

HELP_总览 = """
【重要】可以反复配置：随时再运行本脚本，覆盖写入 src/config.py。

【旧配置迁移】有老工程 config.py 时，先执行：
  python scripts/merge_legacy_config.py --legacy "老路径\\src\\config.py"
  或：python scripts/configure_interactive.py --merge-legacy "老路径\\src\\config.py" --merge-only
  再运行本向导补全 mf_* 鼠标坐标等。

【快速模式】只问数据库 + API + 目录 + 券商进程名/标题；RPA 坐标一律不配，
           并自动关闭「下单前 VLM 截图校验」。适合先让 API 和库跑通。

【标准模式】在快速基础上，多问你：邮件通知、执行器超时、是否现在就要
           填屏幕坐标。若选「暂不填坐标」，与快速同样处理 RPA。

【完整模式】逐项问完（和以前一样多），适合你已经会标定 bbox 时再用。
"""

HELP_目录 = """
去哪找？
  · 开发机工程根：就是你本机这份 trader 文件夹的路径（放代码的地方）。
  · 服务器工程根：云服务器上准备 git clone / 解压后的同一项目路径。
  · Python 路径：服务器上 cmd 里输入 where python 看到的路径；不确定就填 python。
  · NSSM：装 Windows 服务用的 nssm.exe 路径；不用服务就填 nssm 占位即可。
"""

HELP_数据库 = """
去哪找？
  · 问公司运维 / 自己装 MySQL 时的「主机、端口、库名、用户名、密码」。
  · 用 Navicat、DBeaver、MySQL Workbench 连上后，连接属性里都有。
  · 库要先建好表：在本项目根执行  python scripts/run_migrations.py
"""

HELP_API = """
去哪找？
  · 监听地址：云服务器上一般填 0.0.0.0（表示网卡全监听）。
  · 端口：默认 18080，除非你和运维约定了别的。
  · Token：关 HMAC 时用；开 HMAC 时也要填一个备用，可以和密钥里随便一致或单独设。
  · IP 白名单：填「允许访问 API 的机器」的公网 IP。本机测试一定要包含 127.0.0.1。
  · HMAC：和 ems_commander.py（或你的发单程序）里配置的 key_id、secret 必须一致，
          否则云端拒签。数字时间窗一般 300 秒即可。
"""

HELP_RPA_坐标 = """
这些全是「整屏像素坐标」，用 Windows 自带截图或微信截图时看鼠标位置不直观，
建议：浏览器搜「屏幕坐标查看工具」或用 Python 临时跑 pyautogui.position()。

去哪找？怎么量？
  · bbox_pre_trade（左,上,宽,高）：框住左下角「买卖单」里代码+价格+数量那一整块，
    给 VLM 核对用。用截图工具从屏幕最左上角数像素，或看标定教程。
  · confirm_button_xy（x,y）：大红色「买入」或蓝色「卖出」按钮正中心。
  · secondary_confirm_xy：点完买入后弹出「确定」小窗时，点「确定」的中心；没有弹窗就填 none。
  · bbox_order_table：右下角「当日委托」表格那一块区域。
  · withdraw_button_xy：没有截撤单按钮模板时，要点全局「撤单」按钮的中心（有模板可 none）。

更简单的方式：先不配——在向导里选「暂不填坐标」，脚本会关掉 ai_verify；
等有空再运行本脚本选「完整」或手改 config.py 打开校验。

纯鼠标填单（order_ui_mouse_flow=True）不问你「代码后按几次 Tab」；config 里 tabs_* 仅给旧版 Tab 路径留默认值，向导里已固定，不再提问。
"""


@dataclass
class ConfigInputs:
    deploy_dev: str
    deploy_srv: str
    deploy_py: str
    deploy_nssm: str
    db_host: str
    db_port: int
    db_user: str
    db_pass: str
    db_name: str
    notify_mode: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_pass: str
    smtp_receivers: List[str]
    poll_sec: int
    max_retry: int
    batch_size: int
    order_wait: int
    order_poll: int
    email_enabled: bool
    email_host: str
    email_port: int
    email_user: str
    email_pass: str
    email_folder: str
    email_prefix: str
    email_poll: int
    api_host: str
    api_port: int
    api_token: str
    ip_whitelist: List[str]
    hmac_enabled: bool
    hmac_key_id: str
    hmac_secret: str
    max_skew: int
    nonce_ttl: int
    ding_url: str
    vlm_url: str
    vlm_key: str
    vlm_model: str
    broker_exe: str
    broker_proc: str
    win_title_re: str
    ai_verify: bool
    strict_popup: bool
    bbox_pre: Optional[Tuple[int, int, int, int]]
    confirm_xy: Optional[Tuple[int, int]]
    secondary_xy: Optional[Tuple[int, int]]
    tabs_after_code: int
    tabs_market_skip: int
    bbox_order: Optional[Tuple[int, int, int, int]]
    withdraw_xy: Optional[Tuple[int, int]]
    pos_enabled: bool
    bbox_pos: Optional[Tuple[int, int, int, int]]
    pos_tab_hotkey: str
    pos_tab_xy: Optional[Tuple[int, int]]
    status_monitor: bool
    status_poll: float


def build_config_content(inp: ConfigInputs) -> str:
    recv_lit = ", ".join(py_str(x) for x in inp.smtp_receivers)
    ip_lit = ", ".join(py_str(x) for x in inp.ip_whitelist)

    lines: List[str] = []
    ap = lines.append
    ap("# -*- coding: utf-8 -*-")
    ap('"""Global config generated by scripts/configure_interactive.py"""')
    ap("")
    ap("# 开发与服务器目录可能不同；供 NSSM、手工启动 run_ems.py 时对照（键名为英文）")
    ap("DEPLOY_PATHS = {")
    ap(f'    "development_project_root": {py_str(inp.deploy_dev)},')
    ap(f'    "server_project_root": {py_str(inp.deploy_srv)},')
    ap(f'    "server_python_exe": {py_str(inp.deploy_py)},')
    ap(f'    "nssm_exe_hint": {py_str(inp.deploy_nssm)},')
    ap('    "logs_dir_relative": "logs",')
    ap("}")
    ap("")
    ap("DB_CONFIG = {")
    ap(f'    "host": {py_str(inp.db_host)},')
    ap(f"    \"port\": {int(inp.db_port)},")
    ap(f'    "user": {py_str(inp.db_user)},')
    ap(f'    "password": {py_str(inp.db_pass)},')
    ap(f'    "database": {py_str(inp.db_name)},')
    ap('    "charset": "utf8mb4",')
    ap("}")
    ap("")
    ap(f'NOTIFY_MODE = {py_str(inp.notify_mode)}  # "NONE" or "EMAIL"')
    ap("SMTP_CONFIG = {")
    ap(f'    "host": {py_str(inp.smtp_host)},')
    ap(f"    \"port\": {int(inp.smtp_port)},")
    ap(f'    "user": {py_str(inp.smtp_user)},')
    ap(f'    "password": {py_str(inp.smtp_pass)},')
    ap(f"    \"receivers\": [{recv_lit}],")
    ap("}")
    ap("")
    ap(f"POLL_INTERVAL_SECONDS = {int(inp.poll_sec)}")
    ap(f"MAX_RETRY = {int(inp.max_retry)}")
    ap(f"BATCH_SIZE = {int(inp.batch_size)}")
    ap(f"ORDER_WAIT_TIMEOUT = {int(inp.order_wait)}")
    ap(f"ORDER_POLL_INTERVAL = {int(inp.order_poll)}")
    ap("")
    ap("# True：place_order 返回后直接将信号标为 SUCCESS，不轮询 query_order（纯递交阶段）")
    ap("EXECUTOR_SUBMIT_ONLY_MODE = True")
    ap("")
    ap("EMAIL_INGEST_CONFIG = {")
    ap(f'    "enabled": {"True" if inp.email_enabled else "False"},')
    ap(f'    "imap_host": {py_str(inp.email_host)},')
    ap(f"    \"imap_port\": {int(inp.email_port)},")
    ap(f'    "username": {py_str(inp.email_user)},')
    ap(f'    "password": {py_str(inp.email_pass)},')
    ap(f'    "folder": {py_str(inp.email_folder)},')
    ap(f'    "subject_prefix": {py_str(inp.email_prefix)},')
    ap(f"    \"poll_interval_seconds\": {int(inp.email_poll)},")
    ap("}")
    ap("")
    ap("API_CONFIG = {")
    ap(f'    "host": {py_str(inp.api_host)},')
    ap(f"    \"port\": {int(inp.api_port)},")
    ap(f'    "token": {py_str(inp.api_token)},')
    ap(f"    \"ip_whitelist\": [{ip_lit}],")
    ap(f'    "hmac_enabled": {"True" if inp.hmac_enabled else "False"},')
    ap("    \"hmac_keys\": {")
    ap(f'        {py_str(inp.hmac_key_id)}: {py_str(inp.hmac_secret)},')
    ap("    },")
    ap(f"    \"max_skew_seconds\": {int(inp.max_skew)},")
    ap(f"    \"nonce_ttl_seconds\": {int(inp.nonce_ttl)},")
    ap("}")
    ap("")
    ap("RPA_CONFIG = {")
    ap(f'    "broker_exe_path": {py_str(inp.broker_exe)},')
    ap(f'    "broker_process_name": {py_str(inp.broker_proc)},')
    ap("    \"broker_auto_start\": True,")
    ap("    \"broker_start_wait_sec\": 3.0,")
    ap(f'    "main_window_title_re": {py_str(inp.win_title_re)},')
    ap('    "pywinauto_backend": "uia",')
    ap(f'    "dingtalk_webhook_url": {py_str(inp.ding_url)},')
    ap(f'    "vlm_api_url": {py_str(inp.vlm_url)},')
    ap(f'    "vlm_api_key": {py_str(inp.vlm_key)},')
    ap(f'    "vlm_model": {py_str(inp.vlm_model)},')
    ap('    "vlm_chat_mode": "auto",')
    ap('    "vlm_tesseract_lang": "chi_sim+eng",')
    ap('    "vlm_tesseract_cmd": "",')
    ap('    "vlm_tesseract_preprocess": True,')
    ap('    "vlm_tesseract_config": "--oem 3 --psm 6",')
    ap('    "ai_verify_min_ocr_signal": 14,')
    ap(f'    "ai_verify_enabled": {"True" if inp.ai_verify else "False"},')
    ap(f'    "strict_popup_ai": {"True" if inp.strict_popup else "False"},')
    ap('    "window_maximize_button_xy": None,')
    ap('    "after_maximize_wait_sec": 0.3,')
    ap('    "order_ui_mouse_flow": True,')
    ap('    "rpa_submit_sync_after_fill": False,')
    ap('    "mf_click_pause_sec": 0.12,')
    ap('    "mf_direction_click_count": 1,')
    ap('    "mf_direction_between_click_sec": 0.08,')
    ap('    "mf_stock_code_xy": None,')
    ap('    "mf_direction_buy_xy": None,')
    ap('    "mf_direction_sell_xy": None,')
    ap('    "mf_entrust_type_limit_xy": None,')
    ap('    "mf_entrust_type_market_xy": None,')
    ap('    "mf_entrust_type_dropdown_xy": None,')
    ap('    "mf_entrust_type_limit_option_xy": None,')
    ap('    "mf_entrust_type_market_option_xy": None,')
    ap('    "mf_entrust_type_dropdown_open_wait_sec": 0.25,')
    ap('    "mf_price_xy": None,')
    ap('    "mf_skip_price_when_market": True,')
    ap('    "mf_quantity_type_dropdown_xy": None,')
    ap('    "mf_quantity_type_fixed_qty_option_xy": None,')
    ap('    "mf_quantity_type_fixed_amount_option_xy": None,')
    ap('    "mf_quantity_type_position_ratio_option_xy": None,')
    ap('    "mf_quantity_type_dropdown_open_wait_sec": 0.25,')
    ap('    "mf_quantity_type_shares_xy": None,')
    ap('    "mf_quantity_type_ratio_xy": None,')
    ap('    "mf_default_quantity_is_ratio": False,')
    ap('    "mf_quantity_xy": None,')
    ap('    "order_mouse_flow_suppress_post_escape": True,')
    ap(f'    "bbox_pre_trade": {fmt_tuple_int4(inp.bbox_pre)},')
    ap(f'    "confirm_button_xy": {fmt_tuple_int2(inp.confirm_xy)},')
    ap('    "confirm_sell_button_xy": None,')
    ap(f'    "secondary_confirm_xy": {fmt_tuple_int2(inp.secondary_xy)},')
    ap("    \"market_skip_price_field\": True,")
    ap(f"    \"tabs_after_code\": {int(inp.tabs_after_code)},")
    ap(f"    \"tabs_market_skip_price\": {int(inp.tabs_market_skip)},")
    ap(f'    "bbox_order_table": {fmt_tuple_int4(inp.bbox_order)},')
    ap('    "panel_tab_orders_xy": None,')
    ap('    "panel_tab_trades_xy": None,')
    ap('    "panel_tab_funds_xy": None,')
    ap('    "panel_tab_positions_xy": None,')
    ap('    "panel_content_bbox": None,')
    ap('    "panel_content_scroll_anchor_xy": None,')
    ap('    "orders_refresh_hotkey": "F3",')
    ap('    "trades_refresh_hotkey": "F4",')
    ap("    \"orders_table_settle_sec\": 0.55,")
    ap("    \"status_monitor_bring_front\": True,")
    ap(f'    "withdraw_button_xy": {fmt_tuple_int2(inp.withdraw_xy)},')
    ap('    "opencv_templates": {},')
    ap("    \"opencv_match_threshold\": 0.82,")
    ap("    \"opencv_search_margin\": 48,")
    ap("    \"opencv_dead_warn_px\": 50,")
    ap('    "opencv_confirm_template": "buy_confirm_btn",')
    ap('    "opencv_confirm_sell_template": "",')
    ap('    "opencv_withdraw_template": "withdraw_btn",')
    ap('    "opencv_secondary_confirm_template": "secondary_confirm_btn",')
    ap("    \"opencv_withdraw_strip_half_height\": 18,")
    ap(f'    "position_query_enabled": {"True" if inp.pos_enabled else "False"},')
    ap(f'    "bbox_positions_table": {fmt_tuple_int4(inp.bbox_pos)},')
    ap(f'    "positions_tab_hotkey": {py_str(inp.pos_tab_hotkey)},')
    ap(f'    "positions_tab_xy": {fmt_tuple_int2(inp.pos_tab_xy)},')
    ap("    \"after_positions_tab_wait_sec\": 0.4,")
    ap("    \"positions_table_settle_sec\": 0.5,")
    ap("    \"rpa_task_timeout_sec\": 600,")
    ap("    \"after_reset_wait_sec\": 0.35,")
    ap("    \"after_confirm_wait_sec\": 0.6,")
    ap("    \"after_secondary_confirm_wait_sec\": 0.35,")
    ap('    "post_order_success_ok_xy": None,')
    ap('    "opencv_post_success_ok_template": "",')
    ap('    "before_post_order_success_ok_sec": 0.2,')
    ap('    "after_post_order_success_ok_wait_sec": 0.35,')
    ap("    \"post_submit_sync_attempts\": 5,")
    ap("    \"post_submit_sync_interval_sec\": 1.2,")
    ap(f'    "status_monitor_enabled": {"True" if inp.status_monitor else "False"},')
    ap(f"    \"status_poll_interval_sec\": {float(inp.status_poll)},")
    ap("}")
    return "\n".join(lines) + "\n"


def _collect_deploy(root: Path) -> Tuple[str, str, str, str]:
    print_block("一、目录（开发与服务器可以不同）", HELP_目录)
    deploy_dev = ask("本机（开发）工程根目录", str(root))
    deploy_srv = ask("服务器上的工程根目录（和本机一样就回车）", deploy_dev)
    deploy_py = ask("服务器上 Python 可执行文件路径（不知道就填 python）", "python")
    deploy_nssm = ask("NSSM 路径（不用就填 nssm）", "nssm")
    return deploy_dev, deploy_srv, deploy_py, deploy_nssm


def _collect_db() -> Tuple[str, int, str, str, str]:
    print_block("二、MySQL", HELP_数据库)
    db_host = ask("数据库主机", "127.0.0.1")
    db_port = int(ask("端口", "3306"))
    db_user = ask("用户名", "root")
    db_pass = ask("密码", "")
    db_name = ask("库名", "mydb")
    return db_host, db_port, db_user, db_pass, db_name


def _collect_notify_and_executor(模式: str) -> Tuple[str, str, int, str, str, List[str], int, int, int, int, int, bool, str, int, str, str, str, str, int]:
    """返回 notify, smtp*, poll, max_retry, batch, order_wait, order_poll, email_*"""
    smtp_host = smtp_user = smtp_pass = ""
    smtp_port = 465
    smtp_receivers: List[str] = []

    if 模式 == "quick":
        notify_mode = "NONE"
        poll_sec, max_retry, batch_size, order_wait, order_poll = 5, 5, 10, 120, 1
        email_enabled = False
        email_host, email_user, email_pass = "imap.qq.com", "", ""
        email_port, email_folder, email_prefix, email_poll = 993, "INBOX", "EMS_SIGNAL", 15
        print_block("三、系统通知与执行器", "快速模式：已固定为 不发邮件、不拉邮箱信号；轮询 5 秒、等单 120 秒。")
    else:
        print_block("三、系统通知（邮件）", "NONE=不发；EMAIL=发执行通知。去哪找？问运维要 SMTP，或 QQ 邮箱设置里开 SMTP。")
        notify_mode = ask("通知模式 NONE 或 EMAIL", "NONE").upper()
        while notify_mode not in ("NONE", "EMAIL"):
            notify_mode = ask("只能填 NONE 或 EMAIL", "NONE").upper()
        if notify_mode == "EMAIL":
            smtp_host = ask("SMTP 地址", "smtp.qq.com")
            smtp_port = int(ask("SMTP 端口", "465"))
            smtp_user = ask("SMTP 用户名", "")
            smtp_pass = ask("SMTP 密码/授权码", "")
            rec = ask("收件人邮箱，英文逗号分隔", "")
            smtp_receivers = split_csv_tokens(rec) if rec else []

        print_block("四、执行器轮询与等单", "不懂就回车用默认：轮询 5 秒、最多重试 5、一次拉 10 条、等单 120 秒。")
        poll_sec = int(ask("轮询数据库间隔（秒）", "5"))
        max_retry = int(ask("单信号最大重试", "5"))
        batch_size = int(ask("每次最多拉几条信号", "10"))
        order_wait = int(ask("等一笔委托终态最长多少秒", "120"))
        order_poll = int(ask("query_order 间隔（秒）", "1"))

        print_block("五、从邮箱拉信号（一般关）", "除非你用邮件发策略，否则选否。")
        email_enabled = ask_bool("启用邮箱拉信号？", False)
        email_host = "imap.qq.com"
        email_port = 993
        email_user = email_pass = ""
        email_folder = "INBOX"
        email_prefix = "EMS_SIGNAL"
        email_poll = 15
        if email_enabled:
            email_host = ask("IMAP 服务器", "imap.qq.com")
            email_port = int(ask("IMAP 端口", "993"))
            email_user = ask("邮箱账号", "")
            email_pass = ask("邮箱密码", "")
            email_folder = ask("文件夹", "INBOX")
            email_prefix = ask("主题前缀", "EMS_SIGNAL")
            email_poll = int(ask("轮询秒数", "15"))

    return (
        notify_mode,
        smtp_host,
        smtp_port,
        smtp_user,
        smtp_pass,
        smtp_receivers,
        poll_sec,
        max_retry,
        batch_size,
        order_wait,
        order_poll,
        email_enabled,
        email_host,
        email_port,
        email_user,
        email_pass,
        email_folder,
        email_prefix,
        email_poll,
    )


def _collect_api() -> Tuple[str, int, str, List[str], bool, str, str, int, int]:
    print_block("六、API（云端）", HELP_API)
    api_host = ask("监听地址", "0.0.0.0")
    api_port = int(ask("端口", "18080"))
    api_token = ask("Bearer Token（关 HMAC 时用）", "")
    ips = ask("IP 白名单，英文逗号分隔", "127.0.0.1")
    ip_whitelist = split_csv_tokens(ips)
    hmac_enabled = ask_bool("启用 HMAC？", True)
    hmac_key_id = ask("HMAC 密钥 ID（和发单方一致）", "strategy01")
    hmac_secret = ask("HMAC 密钥 Secret", "")
    max_skew = int(ask("时间戳允许偏差（秒）", "300"))
    nonce_ttl = int(ask("Nonce 有效期（秒）", "300"))
    return (
        api_host,
        api_port,
        api_token,
        ip_whitelist,
        hmac_enabled,
        hmac_key_id,
        hmac_secret,
        max_skew,
        nonce_ttl,
    )


def _collect_rpa_common() -> Tuple[str, str, str, str, str, str, str, str]:
    print_block(
        "七、RPA / 券商（基础项）",
        "钉钉：在钉钉群「智能群助手」里加「自定义机器人」复制 Webhook。\n"
        "VLM：填完整接口 URL（…/v1/chat/completions）；根地址可自动补路径。\n"
        "DeepSeek 官方 Chat 不支持传图，向导生成的配置里会用 OCR+文本；请本机安装 Tesseract（建议带中文语言包）。\n"
        "券商 exe：若留空，则只检测进程名、不自动双击启动（推荐，避免 WinError5 拒绝访问）。\n"
        "若已手动打开客户端，务必保持 broker_exe_path 为空或填对，否则会尝试 Popen 拉起 exe。",
    )
    ding_url = ask("钉钉 Webhook（不要就回车空）", "")
    vlm_url = ask("VLM API 地址（如 https://api.deepseek.com/v1/chat/completions；不要就回车）", "")
    vlm_key = ask("VLM API Key", "")
    vlm_model = ask("VLM 模型名", "deepseek-chat")
    broker_exe = ask("券商 exe 全路径（推荐留空：先手动打开客户端再跑 EMS）", "")
    broker_proc = ask("进程名（任务管理器里「名称」列，如 ptrade.exe）", "ptrade.exe")
    win_title_re = ask("主窗口标题里不变的一段，写成正则", "PTrade.*交易")
    return ding_url, vlm_url, vlm_key, vlm_model, broker_exe, broker_proc, win_title_re


def _rpa_skip_coords_defaults() -> Tuple[bool, bool, Optional[Tuple[int, int, int, int]], Optional[Tuple[int, int]], Optional[Tuple[int, int]], int, int, Optional[Tuple[int, int, int, int]], Optional[Tuple[int, int]], bool, Optional[Tuple[int, int, int, int]], str, Optional[Tuple[int, int]], bool, float]:
    """不配坐标：关 ai_verify，关监控，关持仓查询。"""
    return (
        False,
        False,
        None,
        None,
        None,
        0,
        1,
        None,
        None,
        False,
        None,
        "",
        None,
        False,
        10.0,
    )


def _collect_rpa_full() -> Tuple[bool, bool, Optional[Tuple[int, int, int, int]], Optional[Tuple[int, int]], Optional[Tuple[int, int]], int, int, Optional[Tuple[int, int, int, int]], Optional[Tuple[int, int]], bool, Optional[Tuple[int, int, int, int]], str, Optional[Tuple[int, int]], bool, float]:
    print_block("七（续）、RPA 屏幕坐标", HELP_RPA_坐标)
    ai_verify = ask_bool("启用下单前 VLM 截图校验？（是则必须填 bbox_pre_trade）", True)
    strict_popup = ask_bool("弹窗 VLM 解析失败就当阻断？", False)
    print("  坐标格式：四个数 左,上,宽,高 ；没有就输入 none")
    bbox_pre = parse_bbox(ask("bbox_pre_trade", "none"))
    confirm_xy = parse_xy(ask("confirm_button_xy（x,y）", "none"))
    secondary_xy = parse_xy(ask("secondary_confirm_xy", "none"))
    # 纯鼠标流不依赖 Tab；仅兼容旧 Tab 路径的默认值
    tabs_after_code = 0
    tabs_market_skip = 1
    bbox_order = parse_bbox(ask("bbox_order_table", "none"))
    withdraw_xy = parse_xy(ask("withdraw_button_xy", "none"))
    pos_enabled = ask_bool("启用持仓查询（TARGET 用）？", False)
    bbox_pos = parse_bbox(ask("bbox_positions_table", "none"))
    pos_tab_hotkey = ask("持仓页快捷键", "")
    pos_tab_xy = parse_xy(ask("持仓页按钮 x,y", "none"))
    status_monitor = ask_bool("启用委托表定时监控？", False)
    status_poll = float(ask("监控间隔秒", "10"))
    return (
        ai_verify,
        strict_popup,
        bbox_pre,
        confirm_xy,
        secondary_xy,
        tabs_after_code,
        tabs_market_skip,
        bbox_order,
        withdraw_xy,
        pos_enabled,
        bbox_pos,
        pos_tab_hotkey,
        pos_tab_xy,
        status_monitor,
        status_poll,
    )


def main() -> int:
    _configure_stdio_utf8()
    parser = argparse.ArgumentParser(description="EMS 中文配置向导")
    parser.add_argument("--project-root", type=Path, default=None)
    parser.add_argument(
        "--mode",
        choices=("quick", "standard", "full", "ask"),
        default="ask",
        help="quick=快速 standard=标准 full=完整 ask=运行时询问",
    )
    parser.add_argument(
        "--merge-legacy",
        type=Path,
        default=None,
        metavar="PATH",
        help="启动向导前：先把该路径的旧 src/config.py 合并进本工程（备份当前文件）",
    )
    parser.add_argument(
        "--merge-only",
        action="store_true",
        help="与 --merge-legacy 合用：只合并写入，不进入提问向导",
    )
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    root = (args.project_root or script_dir.parent).resolve()

    if args.merge_legacy is not None:
        import importlib.util

        mp = script_dir / "merge_legacy_config.py"
        spec = importlib.util.spec_from_file_location("_merge_cfg_mod", mp)
        if spec is None or spec.loader is None:
            print(f"[错误] 找不到合并脚本: {mp}")
            return 2
        mm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mm)
        try:
            mm.merge_legacy_into_project(args.merge_legacy.resolve(), root, backup=True, dry_run=False)
        except Exception as e:
            print(f"[错误] 合并失败: {type(e).__name__}: {e}")
            return 1
        if args.merge_only:
            print("[完成] 仅合并已写入，未进入向导。可再运行本脚本不加 --merge-only 补配。")
            return 0

    print("=" * 56)
    print("  EMS / RPA 配置向导（可随时重新运行，覆盖 src/config.py）")
    print("=" * 56)
    print(HELP_总览)

    if args.mode == "ask":
        print("\n请选择模式（输入数字）：")
        print("  1 = 快速（最少问题，不配屏幕坐标，关 VLM 下单校验）")
        print("  2 = 标准（比快速多：邮件通知、等单时间、可选是否现在配坐标）")
        print("  3 = 完整（所有项，适合已会标定的人）")
        m = ask("输入 1 / 2 / 3", "1").strip()
        模式 = {"1": "quick", "2": "standard", "3": "full"}.get(m, "quick")
    else:
        模式 = args.mode

    if ask(f"工程根目录将写入配置引用：{root} ，对吗？", "y").lower() not in ("y", "yes", "是", ""):
        print("已退出。可加参数：--project-root \"路径\"")
        return 1

    dd, ds, dp, dn = _collect_deploy(root)
    db = _collect_db()
    nt = _collect_notify_and_executor(模式)
    api = _collect_api()
    ding, vu, vk, vm, be, bp, wt = _collect_rpa_common()

    if 模式 == "quick":
        rpa = _rpa_skip_coords_defaults()
        print_block("RPA 坐标", "快速模式：已关闭 VLM 下单校验，不配任何坐标。之后会用「完整」再配也行。")
    elif 模式 == "standard":
        print_block("RPA 坐标（标准）", "若还不会量像素，下面选「否」。")
        if ask_bool("现在就要配置屏幕坐标和 VLM 校验吗？", False):
            rpa = _collect_rpa_full()
        else:
            rpa = _rpa_skip_coords_defaults()
            print("已按「暂不配坐标」处理：ai_verify=False，坐标全空。")
    else:
        rpa = _collect_rpa_full()

    inp = ConfigInputs(
        deploy_dev=dd,
        deploy_srv=ds,
        deploy_py=dp,
        deploy_nssm=dn,
        db_host=db[0],
        db_port=db[1],
        db_user=db[2],
        db_pass=db[3],
        db_name=db[4],
        notify_mode=nt[0],
        smtp_host=nt[1],
        smtp_port=nt[2],
        smtp_user=nt[3],
        smtp_pass=nt[4],
        smtp_receivers=nt[5],
        poll_sec=nt[6],
        max_retry=nt[7],
        batch_size=nt[8],
        order_wait=nt[9],
        order_poll=nt[10],
        email_enabled=nt[11],
        email_host=nt[12],
        email_port=nt[13],
        email_user=nt[14],
        email_pass=nt[15],
        email_folder=nt[16],
        email_prefix=nt[17],
        email_poll=nt[18],
        api_host=api[0],
        api_port=api[1],
        api_token=api[2],
        ip_whitelist=api[3],
        hmac_enabled=api[4],
        hmac_key_id=api[5],
        hmac_secret=api[6],
        max_skew=api[7],
        nonce_ttl=api[8],
        ding_url=ding,
        vlm_url=vu,
        vlm_key=vk,
        vlm_model=vm,
        broker_exe=be,
        broker_proc=bp,
        win_title_re=wt,
        ai_verify=rpa[0],
        strict_popup=rpa[1],
        bbox_pre=rpa[2],
        confirm_xy=rpa[3],
        secondary_xy=rpa[4],
        tabs_after_code=rpa[5],
        tabs_market_skip=rpa[6],
        bbox_order=rpa[7],
        withdraw_xy=rpa[8],
        pos_enabled=rpa[9],
        bbox_pos=rpa[10],
        pos_tab_hotkey=rpa[11],
        pos_tab_xy=rpa[12],
        status_monitor=rpa[13],
        status_poll=rpa[14],
    )

    if inp.ai_verify and inp.bbox_pre is None:
        print("\n[提示] 已开启 VLM 校验但未填 bbox_pre_trade，下单会报错，已自动改为关闭 ai_verify。")
        inp = replace(inp, ai_verify=False)

    cfg_path = root / "src" / "config.py"
    if not cfg_path.parent.is_dir():
        print(f"[错误] 无目录: {cfg_path.parent}")
        return 2

    try:
        content = build_config_content(inp)
    except ValueError as e:
        print(f"[错误] {e}")
        return 2

    if ask(f"写入 {cfg_path} ？", "y").lower() not in ("y", "yes", "是", ""):
        print("已取消。")
        return 0

    cfg_path.write_text(content, encoding="utf-8")
    print(f"\n[完成] 已写入: {cfg_path}")
    print("\n提醒：")
    print("  · 随时可再运行本脚本或：python scripts/configure_interactive.py --mode full")
    print("  · 坐标标定辅助：python scripts/rpa_calibrate.py")
    print("  · Fernet 密钥：api_server.py 与 ems_commander.py 必须一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
