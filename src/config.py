# -*- coding: utf-8 -*-
"""全局配置"""

DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "your_user",
    "password": "your_password",
    "database": "your_db",
    "charset": "utf8mb4",
}

# 钉钉机器人
DINGTALK_WEBHOOK = "https://oapi.dingtalk.com/robot/send?access_token=xxxx"

# 轮询与重试参数
POLL_INTERVAL_SECONDS = 5
MAX_RETRY = 5
BATCH_SIZE = 10
ORDER_WAIT_TIMEOUT = 12
ORDER_POLL_INTERVAL = 1

# 邮件接入（IMAP）配置
EMAIL_INGEST_CONFIG = {
    "enabled": False,
    "imap_host": "imap.qq.com",
    "imap_port": 993,
    "username": "your_mail@qq.com",
    "password": "your_imap_auth_code",
    "folder": "INBOX",
    "subject_prefix": "EMS_SIGNAL",
    "poll_interval_seconds": 15,
}

# 远程下单API配置
API_CONFIG = {
    "host": "0.0.0.0",
    "port": 18080,
    # 兼容模式Bearer Token（可选）
    "token": "change_me_to_a_strong_token",
    # 可选IP白名单（留空表示不启用）
    "ip_whitelist": [],
    # 安全增强：HMAC签名（推荐）
    "hmac_enabled": True,
    # key_id -> secret，建议改成足够复杂的随机串
    "hmac_keys": {
        "strategy01": "change_me_to_a_very_strong_secret",
    },
    # 请求时间戳允许漂移秒数
    "max_skew_seconds": 300,
    # nonce重放窗口秒数
    "nonce_ttl_seconds": 300,
}
