# -*- coding: utf-8 -*-
r"""
EMS 接入平面自检（与 ems_commander 打的地址一致）

用法（在 scripts 目录或项目根）::

    cd C:\\软件\\trader\\scripts
    python ems_self_check.py

    # 公网在「云主机内部」测经常超时（hairpin），请换下面任一方式：
    python ems_self_check.py --local                    # 只测本机 127（api 已在本机运行时）
    python ems_self_check.py --remote http://127.0.0.1:18080
    # 在办公电脑测监察台同款公网（最准）：
    set EMS_CLOUD_API_BASE=http://120.53.250.208:18080
    python ems_self_check.py

检查项：
  1) GET {REMOTE}/health 是否存在 signal_schema_version==2
  2) --local：本机 http://127.0.0.1:18080/health
  3) 默认：若远端超时且未禁用，会自动再测一遍 127（见 --no-auto-loopback）

说明：
  - 云服务器**里**访问自己的公网 EIP:18080 常 **URLError timed out**，不等于监察台也超时。
  - 最终应以**办公机**访问 CLOUD_API_BASE 为准。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def _fetch_json(url: str, timeout: float) -> tuple[bool, object]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ems-self-check/1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        return True, json.loads(raw)
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return False, f"HTTP {e.code}: {body[:500]}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def check_one(label: str, url: str, timeout: float) -> tuple[int, dict | None, str]:
    """返回 (退出码片段, 成功时的 dict, 失败时的错误文本)。"""
    print(f"\n{'=' * 60}\n{label}\nURL: {url}\n{'=' * 60}")
    ok, data = _fetch_json(url, timeout)
    if not ok:
        err = str(data)
        print(f"[FAIL] {err}")
        return 1, None, err
    if not isinstance(data, dict):
        print(f"[FAIL] 返回非 JSON 对象: {data!r}")
        return 1, None, "invalid_json_type"
    print(json.dumps(data, ensure_ascii=False, indent=2))
    ver = data.get("signal_schema_version")
    path = data.get("signal_ingest_path") or ""
    if ver == 2:
        print("[PASS] signal_schema_version == 2")
        if path:
            print(f"[PASS] signal_ingest_path 已返回（请核对是否为你部署的目录）")
        return 0, data, ""
    if ver is None:
        print("[FAIL] 无 signal_schema_version → 仍是旧接入平面，或公网未打到当前这台 Flask")
    else:
        print(f"[FAIL] signal_schema_version={ver!r}，期望整数 2")
    return 1, data, "schema_mismatch"


def main() -> int:
    parser = argparse.ArgumentParser(description="EMS 接入平面 /health 自检")
    parser.add_argument(
        "--remote",
        default=os.environ.get("EMS_CLOUD_API_BASE", "http://120.53.250.208:18080").strip().rstrip("/"),
        help="与 ems_commander 中 CLOUD_API_BASE 一致（可用环境变量 EMS_CLOUD_API_BASE）",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="只测本机 http://127.0.0.1:18080/health（不测 --remote）",
    )
    parser.add_argument(
        "--no-auto-loopback",
        action="store_true",
        help="远端超时后不要自动再测 127.0.0.1",
    )
    parser.add_argument("--timeout", type=float, default=15.0, help="单次 HTTP 超时秒数")
    args = parser.parse_args()
    base = args.remote.strip().rstrip("/")
    exit_code = 0
    hairpin_note = False

    if args.local:
        rc, _, _ = check_one("本机 127.0.0.1", "http://127.0.0.1:18080/health", args.timeout)
        exit_code |= rc
    else:
        rc, _, err = check_one("远端（与 ems_commander CLOUD_API_BASE 一致）", f"{base}/health", args.timeout)
        exit_code |= rc
        err_l = (err or "").lower()
        timed_out = "timed out" in err_l or "10060" in err_l
        if rc != 0 and timed_out and not args.no_auto_loopback:
            if not base.startswith("http://127.0.0.1") and not base.startswith("http://localhost"):
                print(
                    "\n[说明] 访问公网地址超时。若在**云服务器本机**跑本脚本，访问**自己的公网 EIP** "
                    "常因 hairpin / 路由无法建立，**不代表**办公机上的监察台也会超时。"
                    "\n       请在**办公电脑**再执行同一命令测公网；或在服务器上用："
                    "\n         python ems_self_check.py --local"
                    "\n         python ems_self_check.py --remote http://127.0.0.1:18080"
                )
                hairpin_note = True
                rc2, _, _ = check_one(
                    "自动追加：本机 127.0.0.1（确认本机 api 进程是否正常）",
                    "http://127.0.0.1:18080/health",
                    args.timeout,
                )
                if rc2 == 0:
                    print(
                        "\n[结论] 本机 127 通过、公网在本机测超时 → 多为 hairpin；"
                        "请在办公网测公网:18080/health，能出现 signal_schema_version=2 即监察台可通。"
                    )
                    exit_code = 0
                else:
                    exit_code |= rc2

    print("\n" + "=" * 60)
    if exit_code == 0:
        if hairpin_note:
            print("自检结论：本机 api 正常；公网请在办公机再跑一次以确认监察台路径。")
        else:
            print("自检结论：通过。监察台发 U0 应打到这份新 api（若仍失败再看 HMAC/库表）。")
    else:
        print("自检结论：未全部通过。请按上方 [FAIL] 与提示逐项处理。")
    print("=" * 60 + "\n")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
