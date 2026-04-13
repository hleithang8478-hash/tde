# -*- coding: utf-8 -*-
"""系统通知模块（支持静默/邮件）"""

import traceback
import smtplib
from email.mime.text import MIMEText
from email.header import Header

from src import config


def send_notification(title: str, content: str):
    """统一通知入口。"""
    mode = str(getattr(config, "NOTIFY_MODE", "NONE") or "NONE").upper()

    if mode == "NONE":
        print(f"[NOTIFY][NONE] {title}\n{content}")
        return

    if mode == "EMAIL":
        smtp_cfg = getattr(config, "SMTP_CONFIG", {}) or {}
        host = smtp_cfg.get("host", "")
        port = int(smtp_cfg.get("port", 465) or 465)
        user = smtp_cfg.get("user", "")
        password = smtp_cfg.get("password", "")
        receivers = smtp_cfg.get("receivers", []) or []

        if not host or not user or not password or not receivers:
            print("[NOTIFY][EMAIL] SMTP配置不完整，跳过发送")
            return

        msg = MIMEText(content, "plain", "utf-8")
        msg["Subject"] = Header(title, "utf-8")
        msg["From"] = user
        msg["To"] = ", ".join(receivers)

        try:
            with smtplib.SMTP_SSL(host=host, port=port, timeout=10) as smtp:
                smtp.login(user, password)
                smtp.sendmail(user, receivers, msg.as_string())
            print(f"[NOTIFY][EMAIL] 发送成功: {title}")
        except Exception:
            print("[NOTIFY][EMAIL] 发送异常:")
            print(traceback.format_exc())
        return

    print(f"[NOTIFY] 未知模式: {mode}，已跳过。")
