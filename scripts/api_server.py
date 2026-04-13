# -*- coding: utf-8 -*-
"""远程下单API服务（云端运行） - 加密防抓包版"""

import os
import sys
import json
import traceback
from datetime import datetime
from cryptography.fernet import Fernet

from flask import Flask, jsonify, request
from sqlalchemy import create_engine, text

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.config import DB_CONFIG, API_CONFIG
from src.core.repository import SignalRepository
from src.signal_ingest import insert_signal, SignalValidationError
from src.security.auth import ReplayCache, stable_json_body, verify_signature

# ================= 新增加密配置 =================
# 必须和本地指挥部保持一致的 32 字节 Base64 编码密钥
ENCRYPTION_KEY = b'9T71nh6mIWjZIm96LKuYK3-u3AEv5RhiqPWEorKJRcQ='
CIPHER_SUITE = Fernet(ENCRYPTION_KEY)
# ===============================================

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

    if API_CONFIG.get("hmac_enabled", False):
        key_id = request.headers.get("X-API-KEY", "").strip()
        signature = request.headers.get("X-SIGNATURE", "").strip()
        ts = request.headers.get("X-TIMESTAMP", "").strip()
        nonce = request.headers.get("X-NONCE", "").strip()

        # 注意：这里的 payload_obj 已经是包含 encrypted_payload 的包装对象了
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

    auth_header = request.headers.get("Authorization", "")
    token = ""
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()

    if not _is_token_valid(token):
        return False, "Invalid token", 401

    return True, "ok", 200

def decrypt_payload(encrypted_dict):
    """解密传入的 payload"""
    try:
        encrypted_text = encrypted_dict.get("encrypted_payload")
        if not encrypted_text:
            raise ValueError("Missing encrypted_payload")
        decrypted_bytes = CIPHER_SUITE.decrypt(encrypted_text.encode())
        return json.loads(decrypted_bytes.decode('utf-8'))
    except Exception as e:
        raise ValueError("Decryption failed or invalid payload format")

@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "ems-api", "time": datetime.now().isoformat()})

@app.route('/signals', methods=['GET', 'POST'])
def handle_signals():
    if request.method == 'GET':
        client_ip = _client_ip()
        if not _is_ip_allowed(client_ip):
            return _auth_failed("IP not allowed", 403)
            
        try:
            with ENGINE.connect() as conn:
                sql = text("SELECT * FROM trade_signals LIMIT 20")
                result = conn.execute(sql)
                return jsonify([dict(row) for row in result.mappings()])
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    try:
        # 抓包看到的是: {"encrypted_payload": "gAAAAABk..."}
        wrapped_payload = request.get_json(silent=True)
        if not isinstance(wrapped_payload, dict):
            return jsonify({"ok": False, "error": "JSON body is required"}), 400

        # 先验证签名 (验证的是密文的签名，防止被篡改密文)
        ok, reason, code = _authorize_request(wrapped_payload)
        if not ok:
            return _auth_failed(reason, code)

        # 解密获取真正的交易指令 ({"stock_code": "...", "action": "..."})
        try:
            real_payload = decrypt_payload(wrapped_payload)
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400

        # 执行原有逻辑
        signal_id = insert_signal(REPO, real_payload)
        return jsonify({"ok": True, "signal_id": signal_id})

    except SignalValidationError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        print("[API ERROR] /signals POST exception")
        print(traceback.format_exc())
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500

@app.post("/signals/batch")
def create_signal_batch():
    try:
        wrapped_payload = request.get_json(silent=True)
        if not isinstance(wrapped_payload, dict): # 注意这里改成了 dict，因为外层包了一层
            return jsonify({"ok": False, "error": "JSON object body is required"}), 400

        ok, reason, code = _authorize_request(wrapped_payload)
        if not ok:
            return _auth_failed(reason, code)

        try:
            real_payload_list = decrypt_payload(wrapped_payload)
            if not isinstance(real_payload_list, list):
                raise ValueError("Decrypted payload must be a list")
        except ValueError as e:
             return jsonify({"ok": False, "error": str(e)}), 400

        result = []
        for i, item in enumerate(real_payload_list, 1):
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