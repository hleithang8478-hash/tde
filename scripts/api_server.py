# -*- coding: utf-8 -*-
"""远程下单API服务（云端运行）"""

import os
import sys
import traceback
from datetime import datetime

from flask import Flask, jsonify, request
from sqlalchemy import create_engine

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.config import DB_CONFIG, API_CONFIG
from src.core.repository import SignalRepository
from src.signal_ingest import insert_signal, SignalValidationError
from src.security.auth import ReplayCache, stable_json_body, verify_signature


app = Flask(__name__)


def create_db_engine():
    db_url = (
        f"mysql+pymysql://{DB_CONFIG['user']}:{DB_CONFIG['password']}"
        f"@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}?charset={DB_CONFIG['charset']}"
    )
    return create_engine(db_url, pool_pre_ping=True, future=True)


ENGINE = create_db_engine()
REPO = SignalRepository(ENGINE)
REPLAY_CACHE = ReplayCache(ttl_seconds=int(API_CONFIG.get("nonce_ttl_seconds", 300)))


def _client_ip() -> str:
    # 如果后续挂在反向代理后，请改为读取可信 X-Forwarded-For
    return request.remote_addr or ""


def _is_ip_allowed(ip: str) -> bool:
    whitelist = API_CONFIG.get("ip_whitelist", [])
    if not whitelist:
        return True
    return ip in whitelist


def _is_token_valid(token: str) -> bool:
    return bool(token) and token == API_CONFIG.get("token")


def _auth_failed(message: str, status_code: int = 401):
    return jsonify({"ok": False, "error": message}), status_code


def _authorize_request(payload_obj):
    client_ip = _client_ip()
    if not _is_ip_allowed(client_ip):
        return False, "IP not allowed", 403

    # 优先使用HMAC签名模式
    if API_CONFIG.get("hmac_enabled", False):
        key_id = request.headers.get("X-API-KEY", "").strip()
        signature = request.headers.get("X-SIGNATURE", "").strip()
        ts = request.headers.get("X-TIMESTAMP", "").strip()
        nonce = request.headers.get("X-NONCE", "").strip()

        body_text = stable_json_body(payload_obj)

        ok, reason = verify_signature(
            api_keys=API_CONFIG.get("hmac_keys", {}),
            key_id=key_id,
            signature=signature,
            method=request.method,
            path=request.path,
            ts=ts,
            nonce=nonce,
            body_text=body_text,
            max_skew_seconds=int(API_CONFIG.get("max_skew_seconds", 300)),
            replay_cache=REPLAY_CACHE,
        )
        if not ok:
            return False, reason, 401
        return True, "ok", 200

    # 兼容：Bearer Token
    auth_header = request.headers.get("Authorization", "")
    token = ""
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()

    if not _is_token_valid(token):
        return False, "Invalid token", 401

    return True, "ok", 200


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "ems-api", "time": datetime.now().isoformat()})


@app.post("/signals")
def create_signal():
    """
    单条信号入库

    HMAC鉴权请求头（推荐）：
      X-API-KEY: strategy01
      X-TIMESTAMP: 1710000000
      X-NONCE: random-uuid
      X-SIGNATURE: hex(hmac_sha256(secret, sign_message))
    sign_message = METHOD + "\n" + PATH + "\n" + TIMESTAMP + "\n" + NONCE + "\n" + SHA256(JSON_BODY)

    兼容模式：
      Authorization: Bearer <token>
    """
    try:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"ok": False, "error": "JSON body is required"}), 400

        ok, reason, code = _authorize_request(payload)
        if not ok:
            return _auth_failed(reason, code)

        signal_id = insert_signal(REPO, payload)
        return jsonify({"ok": True, "signal_id": signal_id})

    except SignalValidationError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        print("[API ERROR] /signals exception")
        print(traceback.format_exc())
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.post("/signals/batch")
def create_signal_batch():
    """批量创建信号"""
    try:
        payload = request.get_json(silent=True)
        if not isinstance(payload, list):
            return jsonify({"ok": False, "error": "JSON array body is required"}), 400

        ok, reason, code = _authorize_request(payload)
        if not ok:
            return _auth_failed(reason, code)

        result = []
        for i, item in enumerate(payload, 1):
            try:
                signal_id = insert_signal(REPO, item)
                result.append({"index": i, "ok": True, "signal_id": signal_id})
            except SignalValidationError as e:
                result.append({"index": i, "ok": False, "error": str(e)})
            except Exception as e:
                result.append({"index": i, "ok": False, "error": f"{type(e).__name__}: {e}"})

        all_ok = all(x.get("ok") for x in result)
        return jsonify({"ok": all_ok, "items": result})

    except Exception as e:
        print("[API ERROR] /signals/batch exception")
        print(traceback.format_exc())
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


def main():
    host = API_CONFIG.get("host", "0.0.0.0")
    port = int(API_CONFIG.get("port", 18080))
    print(f"[API] ems-api start at http://{host}:{port}")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    main()
