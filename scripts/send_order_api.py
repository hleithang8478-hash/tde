# -*- coding: utf-8 -*-
"""本地/策略机调用示例：通过HTTP推送信号到云端API（支持HMAC签名）"""

import argparse
import hashlib
import hmac
import json
import time
import uuid

import requests


def stable_json_body(body_obj) -> str:
    return json.dumps(body_obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def body_sha256(body_text: str) -> str:
    return hashlib.sha256(body_text.encode("utf-8")).hexdigest()


def build_sign_message(method: str, path: str, ts: str, nonce: str, body_hash: str) -> str:
    return "\n".join([method.upper(), path, ts, nonce, body_hash])


def calc_hmac_sha256(secret: str, message: str) -> str:
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def parse_path_from_url(url: str) -> str:
    # 例如 http://1.2.3.4:18080/signals -> /signals
    if "/" not in url.replace("http://", "", 1).replace("https://", "", 1):
        return "/"
    idx = url.find("/", url.find("//") + 2)
    if idx < 0:
        return "/"
    return url[idx:]


def main():
    parser = argparse.ArgumentParser(description="Send order signal to EMS API")
    parser.add_argument("--url", required=True, help="例如: http://<云服务器IP>:18080/signals")

    parser.add_argument("--auth_mode", default="hmac", choices=["hmac", "token"])
    parser.add_argument("--token", default="", help="Bearer token (auth_mode=token时使用)")

    parser.add_argument("--api_key", default="strategy01", help="HMAC key id")
    parser.add_argument("--api_secret", default="", help="HMAC secret")

    parser.add_argument("--stock_code", required=True, help="如 600519.SH")
    parser.add_argument("--signal_type", default="ORDER", choices=["ORDER", "TARGET"])
    parser.add_argument("--action", default="BUY", choices=["BUY", "SELL"])
    parser.add_argument("--volume", type=int, required=True)
    parser.add_argument("--price_type", default="MARKET", choices=["MARKET", "LIMIT"])
    args = parser.parse_args()

    payload = {
        "stock_code": args.stock_code,
        "signal_type": args.signal_type,
        "action": args.action,
        "volume": args.volume,
        "price_type": args.price_type,
    }

    body_text = stable_json_body(payload)
    headers = {"Content-Type": "application/json"}

    if args.auth_mode == "hmac":
        if not args.api_secret:
            raise ValueError("auth_mode=hmac 时必须提供 --api_secret")

        ts = str(int(time.time()))
        nonce = str(uuid.uuid4())
        path = parse_path_from_url(args.url)
        msg = build_sign_message("POST", path, ts, nonce, body_sha256(body_text))
        signature = calc_hmac_sha256(args.api_secret, msg)

        headers.update(
            {
                "X-API-KEY": args.api_key,
                "X-TIMESTAMP": ts,
                "X-NONCE": nonce,
                "X-SIGNATURE": signature,
            }
        )
    else:
        if not args.token:
            raise ValueError("auth_mode=token 时必须提供 --token")
        headers["Authorization"] = f"Bearer {args.token}"

    resp = requests.post(args.url, headers=headers, data=body_text.encode("utf-8"), timeout=8)
    print(resp.status_code)
    print(resp.text)


if __name__ == "__main__":
    main()
