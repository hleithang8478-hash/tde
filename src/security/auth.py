# -*- coding: utf-8 -*-
"""API签名鉴权与防重放"""

import hashlib
import hmac
import json
import time
from collections import OrderedDict
from typing import Dict, Optional, Tuple


class ReplayCache:
    """进程内防重放缓存（单机部署足够）"""

    def __init__(self, ttl_seconds: int = 300, max_items: int = 10000):
        self.ttl_seconds = ttl_seconds
        self.max_items = max_items
        self._store = OrderedDict()

    def seen_or_add(self, key: str) -> bool:
        now = int(time.time())
        self._cleanup(now)
        if key in self._store:
            return True
        self._store[key] = now + self.ttl_seconds
        if len(self._store) > self.max_items:
            self._store.popitem(last=False)
        return False

    def _cleanup(self, now_ts: int):
        remove_keys = []
        for k, expire_at in self._store.items():
            if expire_at < now_ts:
                remove_keys.append(k)
            else:
                break
        for k in remove_keys:
            self._store.pop(k, None)



def stable_json_body(body: Optional[Dict]) -> str:
    if body is None:
        return ""
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))



def body_sha256(body_text: str) -> str:
    return hashlib.sha256(body_text.encode("utf-8")).hexdigest()



def build_sign_message(method: str, path: str, ts: str, nonce: str, body_hash: str) -> str:
    return "\n".join([method.upper(), path, ts, nonce, body_hash])



def calc_hmac_sha256(secret: str, message: str) -> str:
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()



def verify_signature(
    api_keys: Dict[str, str],
    key_id: str,
    signature: str,
    method: str,
    path: str,
    ts: str,
    nonce: str,
    body_text: str,
    max_skew_seconds: int,
    replay_cache: ReplayCache,
) -> Tuple[bool, str]:
    if not key_id or key_id not in api_keys:
        return False, "invalid key id"

    if not signature:
        return False, "missing signature"

    if not ts.isdigit():
        return False, "invalid ts"

    now_ts = int(time.time())
    req_ts = int(ts)
    if abs(now_ts - req_ts) > max_skew_seconds:
        return False, "timestamp expired"

    if not nonce or len(nonce) < 8:
        return False, "invalid nonce"

    replay_key = f"{key_id}:{ts}:{nonce}"
    if replay_cache.seen_or_add(replay_key):
        return False, "replay request"

    msg = build_sign_message(method, path, ts, nonce, body_sha256(body_text))
    expected = calc_hmac_sha256(api_keys[key_id], msg)
    if not hmac.compare_digest(expected, signature):
        return False, "bad signature"

    return True, "ok"


def verify_signature_body_candidates(
    api_keys: Dict[str, str],
    key_id: str,
    signature: str,
    method: str,
    path: str,
    ts: str,
    nonce: str,
    body_candidates: list[str],
    max_skew_seconds: int,
    replay_cache: ReplayCache,
) -> Tuple[bool, str]:
    """对多段候选 body 串行验 HMAC，任一命中后再做防重放（避免多次调用 verify_signature 时 replay 已被占用）。"""
    if not key_id or key_id not in api_keys:
        return False, "invalid key id"

    if not signature:
        return False, "missing signature"

    if not ts.isdigit():
        return False, "invalid ts"

    now_ts = int(time.time())
    req_ts = int(ts)
    if abs(now_ts - req_ts) > max_skew_seconds:
        return False, "timestamp expired"

    if not nonce or len(nonce) < 8:
        return False, "invalid nonce"

    secret = api_keys[key_id]
    matched = False
    for body_text in body_candidates:
        if body_text is None:
            continue
        msg = build_sign_message(method, path, ts, nonce, body_sha256(body_text))
        expected = calc_hmac_sha256(secret, msg)
        if hmac.compare_digest(expected, signature):
            matched = True
            break

    if not matched:
        return False, "bad signature"

    replay_key = f"{key_id}:{ts}:{nonce}"
    if replay_cache.seen_or_add(replay_key):
        return False, "replay request"

    return True, "ok"
