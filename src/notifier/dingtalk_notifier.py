# -*- coding: utf-8 -*-
"""钉钉消息推送模块"""

import json
import traceback
import requests


def send_dingtalk(webhook_url: str, content: str, timeout: int = 5):
    """
    钉钉机器人通知函数（文本消息）
    :param webhook_url: 钉钉机器人Webhook地址
    :param content: 消息内容
    :param timeout: 请求超时秒数
    """
    if not webhook_url:
        return

    headers = {"Content-Type": "application/json;charset=utf-8"}
    payload = {
        "msgtype": "text",
        "text": {"content": content}
    }

    try:
        resp = requests.post(webhook_url, headers=headers, data=json.dumps(payload), timeout=timeout)
        if resp.status_code != 200:
            print(f"[DINGTALK] HTTP异常: {resp.status_code}, body={resp.text}")
            return

        data = resp.json()
        if data.get("errcode") != 0:
            print(f"[DINGTALK] 业务异常: {data}")
    except Exception:
        print("[DINGTALK] 推送异常:")
        print(traceback.format_exc())
