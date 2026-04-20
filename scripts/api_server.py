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
import src.signal_ingest as _signal_ingest_mod
from src.security.auth import (
    ReplayCache,
    stable_json_body,
    verify_signature,
    verify_signature_body_candidates,
)

print(
    "[EMS-API] signal_ingest loaded:",
    getattr(_signal_ingest_mod, "__file__", "?"),
    "SIGNAL_SCHEMA_VERSION=",
    getattr(_signal_ingest_mod, "SIGNAL_SCHEMA_VERSION", 0),
    flush=True,
)


def _ingest_debug_suffix() -> str:
    """校验失败时附在 error 后，便于区分「旧进程 / 错路径的 signal_ingest」。"""
    try:
        p = getattr(_signal_ingest_mod, "__file__", "?")
        v = getattr(_signal_ingest_mod, "SIGNAL_SCHEMA_VERSION", 0)
        return f" | ingest_file={p} | signal_schema_version={v}"
    except Exception:
        return ""

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

# 与本地指挥部一致的「遥测」鉴权头命名（避免 X-API-KEY / X-SIGNATURE 等典型 API 指纹）
HDR_INSTALL_ID = "X-Installation-Id"
HDR_BODY_SIG = "X-Body-Signature"
HDR_REQ_TS = "X-Request-Timestamp"
HDR_CORRELATION = "X-Correlation-Id"

PATH_TELEMETRY_BEAT = "/v1/agent/telemetry/beat"
PATH_TELEMETRY_SYNC = "/v1/agent/telemetry/sync"
PATH_TELEMETRY_CONFIG = "/v1/agent/telemetry/config"


def _effective_hmac_keys() -> dict:
    """与 ems_commander 对齐：可用 EMS_HMAC_SECRET + EMS_HMAC_KEY_ID 覆盖对应 key 的密钥（无需改 config 文件）。"""
    keys = dict(API_CONFIG.get("hmac_keys") or {})
    sec = os.environ.get("EMS_HMAC_SECRET", "").strip()
    if sec:
        kid = os.environ.get("EMS_HMAC_KEY_ID", "strategy01").strip() or "strategy01"
        keys[kid] = sec
    return keys


def _hmac_body_candidates(raw_text: str, payload_obj: dict) -> list[str]:
    """与指挥部 json.dumps 可能不一致的边界：BOM、末尾换行、中间层改写字节后再 canonical。"""
    variants: list[str] = []
    t = raw_text or ""
    if t.startswith("\ufeff"):
        t = t[1:]
    variants.append(t)
    t2 = t.rstrip("\r\n")
    if t2 != t:
        variants.append(t2)
    try:
        canon = stable_json_body(payload_obj)
        variants.append(canon)
    except Exception:
        pass
    out: list[str] = []
    seen: set[str] = set()
    for v in variants:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


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

def _authorize_request(
    payload_obj,
    body_text: str | None = None,
    sign_path: str | None = None,
):
    client_ip = _client_ip()
    if not _is_ip_allowed(client_ip):
        return False, "IP not allowed", 403

    if API_CONFIG.get("hmac_enabled", False):
        key_id = request.headers.get(HDR_INSTALL_ID, "").strip()
        signature = request.headers.get(HDR_BODY_SIG, "").strip()
        ts = request.headers.get(HDR_REQ_TS, "").strip()
        nonce = request.headers.get(HDR_CORRELATION, "").strip()

        # 必须与 ems_commander 签名的 path 字符串一致；部分反代会改写 PATH_INFO，勿仅用 request.path。
        path_for_sign = sign_path if sign_path is not None else request.path

        keys = _effective_hmac_keys()
        max_skew = int(API_CONFIG.get("max_skew_seconds", 300))

        if body_text is not None:
            # 原始 UTF-8 解码串 + 若干规范化候选，任一与客户端 body_hash 一致即通过（防重放只在命中后登记一次）
            ok, reason = verify_signature_body_candidates(
                api_keys=keys,
                key_id=key_id,
                signature=signature,
                method=request.method,
                path=path_for_sign,
                ts=ts,
                nonce=nonce,
                body_candidates=_hmac_body_candidates(body_text, payload_obj),
                max_skew_seconds=max_skew,
                replay_cache=REPLAY_CACHE,
            )
        else:
            body_text = stable_json_body(payload_obj)
            ok, reason = verify_signature(
                api_keys=keys,
                key_id=key_id,
                signature=signature,
                method=request.method,
                path=path_for_sign,
                ts=ts,
                nonce=nonce,
                body_text=body_text,
                max_skew_seconds=max_skew,
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
    """从遥测外形 JSON 中取出 sync.cursor（Fernet 密文）并解密。"""
    try:
        sync = encrypted_dict.get("sync")
        if not isinstance(sync, dict):
            raise ValueError("Missing sync block")
        encrypted_text = sync.get("cursor")
        if not encrypted_text:
            raise ValueError("Missing sync cursor")
        decrypted_bytes = CIPHER_SUITE.decrypt(str(encrypted_text).encode())
        return json.loads(decrypted_bytes.decode("utf-8"))
    except ValueError:
        raise
    except Exception:
        raise ValueError("Decryption failed or invalid payload format")


@app.get("/health")
def health():
    """含 signal_schema_version / signal_ingest_path，便于核对云端是否加载了带 UI_READ 的代码。"""
    payload = {
        "ok": True,
        "service": "update-health",
        "time": datetime.now().isoformat(),
        "signal_schema_version": getattr(_signal_ingest_mod, "SIGNAL_SCHEMA_VERSION", 0),
        "signal_ingest_path": getattr(_signal_ingest_mod, "__file__", ""),
    }
    # 不泄露密钥：仅长度与 key_id，便于办公室 curl /health?auth=1 与指挥部配置对照
    if request.args.get("auth") == "1":
        hk = _effective_hmac_keys()
        kid = (os.environ.get("EMS_HMAC_KEY_ID", "strategy01").strip() or "strategy01")
        sec = str(hk.get(kid) or hk.get("strategy01") or "")
        payload["hmac_diag"] = {
            "enabled": bool(API_CONFIG.get("hmac_enabled")),
            "key_id": kid,
            "secret_len": len(sec),
            "env_secret_override": bool(os.environ.get("EMS_HMAC_SECRET", "").strip()),
            "sign_path_beat": PATH_TELEMETRY_BEAT,
        }
    return jsonify(payload)


@app.get(PATH_TELEMETRY_CONFIG)
def telemetry_pull_config():
    """伪装为拉取客户端配置；供监察台拉会话列表（须含最新 signal_id + ui_ocr_text）。

    历史 bug：无 ORDER BY 且 LIMIT 20，EMS 已更新的行常不在返回集内 → 前台永远「找不到流水号」。
    """
    client_ip = _client_ip()
    if not _is_ip_allowed(client_ip):
        return _auth_failed("IP not allowed", 403)
    try:
        raw_lim = os.environ.get("EMS_TELEMETRY_PULL_LIMIT", "500").strip()
        try:
            lim = int(raw_lim)
        except ValueError:
            lim = 500
        lim = max(50, min(lim, 3000))
        with ENGINE.connect() as conn:
            sql = text(
                "SELECT * FROM trade_signals ORDER BY signal_id DESC LIMIT :lim"
            )
            result = conn.execute(sql, {"lim": lim})
            rows = [dict(row) for row in result.mappings()]
            return jsonify(rows)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.post(PATH_TELEMETRY_BEAT)
def telemetry_heartbeat():
    """单笔指令：外观为周期性心跳上报。"""
    try:
        raw = request.get_data(cache=True)
        if not raw:
            return jsonify({"ok": False, "error": "JSON body is required"}), 400
        try:
            body_text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return jsonify({"ok": False, "error": "invalid body encoding"}), 400
        try:
            wrapped_payload = json.loads(body_text)
        except json.JSONDecodeError:
            return jsonify({"ok": False, "error": "invalid JSON"}), 400
        if not isinstance(wrapped_payload, dict):
            return jsonify({"ok": False, "error": "JSON body is required"}), 400

        ok, reason, code = _authorize_request(
            wrapped_payload,
            body_text=body_text,
            sign_path=PATH_TELEMETRY_BEAT,
        )
        if not ok:
            return _auth_failed(reason, code)

        try:
            real_payload = decrypt_payload(wrapped_payload)
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400

        signal_id = insert_signal(REPO, real_payload)
        return jsonify({"ok": True, "signal_id": signal_id, "next_poll_sec": 3600})

    except SignalValidationError as e:
        return jsonify({"ok": False, "error": str(e) + _ingest_debug_suffix()}), 400
    except Exception as e:
        print(f"[API ERROR] {PATH_TELEMETRY_BEAT} exception")
        print(traceback.format_exc())
        err = f"{type(e).__name__}: {e}"
        if "signal" in err.lower() or "trade_signals" in err.lower():
            err += _ingest_debug_suffix()
        return jsonify({"ok": False, "error": err}), 500


@app.post(PATH_TELEMETRY_SYNC)
def telemetry_sync_batch():
    """批量指令：外观为状态同步。"""
    try:
        raw = request.get_data(cache=True)
        if not raw:
            return jsonify({"ok": False, "error": "JSON object body is required"}), 400
        try:
            body_text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return jsonify({"ok": False, "error": "invalid body encoding"}), 400
        try:
            wrapped_payload = json.loads(body_text)
        except json.JSONDecodeError:
            return jsonify({"ok": False, "error": "invalid JSON"}), 400
        if not isinstance(wrapped_payload, dict):
            return jsonify({"ok": False, "error": "JSON object body is required"}), 400

        ok, reason, code = _authorize_request(
            wrapped_payload,
            body_text=body_text,
            sign_path=PATH_TELEMETRY_SYNC,
        )
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
                result.append({"index": i, "ok": False, "error": str(e) + _ingest_debug_suffix()})
            except Exception as e:
                result.append({"index": i, "ok": False, "error": f"{type(e).__name__}: {e}"})

        all_ok = all(x.get("ok") for x in result)
        return jsonify({"ok": all_ok, "items": result, "next_poll_sec": 3600})

    except Exception as e:
        print(f"[API ERROR] {PATH_TELEMETRY_SYNC} exception")
        print(traceback.format_exc())
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500

def main():
    host = API_CONFIG.get("host", "0.0.0.0")
    port = int(API_CONFIG.get("port", 18080))
    print(f"[API] ems-api start at http://{host}:{port}")
    _hk = _effective_hmac_keys()
    _kid = (os.environ.get("EMS_HMAC_KEY_ID", "strategy01").strip() or "strategy01")
    _sec = str(_hk.get(_kid) or _hk.get("strategy01") or "")
    print(
        "[EMS-API] HMAC key",
        _kid,
        "secret len=",
        len(_sec),
        "env_secret=",
        bool(os.environ.get("EMS_HMAC_SECRET", "").strip()),
        "sign_path=",
        PATH_TELEMETRY_BEAT,
        PATH_TELEMETRY_SYNC,
        flush=True,
    )
    app.run(host=host, port=port, debug=False)

if __name__ == "__main__":
    main()