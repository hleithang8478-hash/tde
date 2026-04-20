import json
import logging
import os
import sys
import pandas as pd
from flask import Flask, render_template_string, request, jsonify
import requests
import time
import hmac
import hashlib
from cryptography.fernet import Fernet

LOG = logging.getLogger("ems_commander")
if not LOG.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    LOG.addHandler(_h)
LOG.setLevel(logging.INFO)
if os.environ.get("EMS_COMMANDER_LOG_DEBUG", "").strip().lower() in ("1", "true", "yes"):
    LOG.setLevel(logging.DEBUG)

# 保证可 import src.*（本机直写库等）
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ================= 配置区 =================
# ---------- 典型部署（与你环境一致）----------
#   服务器：除本文件外的整仓（api_server、EMS、src、SQL、MySQL 等）；对外提供 18080 接入平面，EMS 轮询库里的 trade_signals。
#   本地：往往只跑本文件 ems_commander（监察台）；浏览器只访问本机 Flask，由本机去 HTTP 请求下面的 CLOUD_API_BASE（服务器）。
#   因此：U0 / 发单 / 列表 等行为**一律以服务器上实际监听的 api 为准**；本地不存在第二份「接入平面」除非你用 EMS_CLOUD_API_BASE 指到本机做联调。
#
# 接入平面根地址（不含路径）。可用环境变量 EMS_CLOUD_API_BASE 覆盖。
# 若「监察台」与 api_server **在同一台 Windows 上**跑：务必设为 http://127.0.0.1:18080 ，
# 否则本机进程去请求自己的公网 EIP 常 hairpin 超时，和脚本自检现象一致。
#   PowerShell: $env:EMS_CLOUD_API_BASE="http://127.0.0.1:18080"
CLOUD_API_BASE = (os.environ.get("EMS_CLOUD_API_BASE") or "http://120.53.250.208:18080").strip().rstrip("/")
# UI_READ 直连库：环境变量 EMS_UI_READ_INSERT_MODE=direct，或 src.config.UI_READ_INSERT_VIA_DB_ONLY=True
# 访问云端时是否使用系统代理（HTTP_PROXY 等）。仅当「直连云 IP 根本不通、必须走代理出网」时改为 True。
CLOUD_TRUST_ENV_PROXY = False
CLOUD_REQUEST_TIMEOUT = 15
# 若日志出现 ConnectTimeout：说明本机 TCP 连不上 云IP:18080（与安全组/防火墙/服务未监听有关，与 MySQL 3306 无关）。
# 请在云控制台放行入站 TCP 18080、本机浏览器试 http://云IP:18080/health、服务器上确认 EMS_API 已启动且 netstat 可见 18080。
PATH_TELEMETRY_BEAT = "/v1/agent/telemetry/beat"
PATH_TELEMETRY_SYNC = "/v1/agent/telemetry/sync"
PATH_TELEMETRY_CONFIG = "/v1/agent/telemetry/config"
# 设备标识（仅出现在遥测 JSON 中，与交易无关；可自行改成固定 GUID）
TELEMETRY_DEVICE_ID = "7c9e2b4a-1d8f-4e3c-9a62-b0f81e2d4c99"

# 与云端一致：优先与同仓 src.config 单一真源对齐；仅拷贝本单文件无 src 时用下方回退（须与服务器 config 相同）。
try:
    from src.config import EMS_HMAC_KEY_ID_DEFAULT, EMS_HMAC_SECRET_DEFAULT

    _HMAC_KEY_FALLBACK = EMS_HMAC_KEY_ID_DEFAULT
    _HMAC_SECRET_FALLBACK = EMS_HMAC_SECRET_DEFAULT
except ImportError:
    _HMAC_KEY_FALLBACK = "strategy01"
    _HMAC_SECRET_FALLBACK = "Qwer!1234567"

API_KEY = (os.environ.get("EMS_HMAC_KEY_ID") or _HMAC_KEY_FALLBACK).strip()
API_SECRET = (os.environ.get("EMS_HMAC_SECRET") or _HMAC_SECRET_FALLBACK).strip()
LOG.info(
    "telemetry HMAC key_id=%s secret_len=%s（须与云端 GET /health?auth=1 里 hmac_diag.secret_len 一致）",
    API_KEY,
    len(API_SECRET),
)

# 必须和云端完全一致的密钥！
ENCRYPTION_KEY = b'9T71nh6mIWjZIm96LKuYK3-u3AEv5RhiqPWEorKJRcQ='
CIPHER_SUITE = Fernet(ENCRYPTION_KEY)
DETAIL_VIEW_PASSWORD = "123456"
# =========================================

app = Flask(__name__)

DETAIL_CACHE = {}

# 与 scripts/api_server.py 中鉴权头名称一致
_HDR_INSTALL_ID = "X-Installation-Id"
_HDR_BODY_SIG = "X-Body-Signature"
_HDR_REQ_TS = "X-Request-Timestamp"
_HDR_CORRELATION = "X-Correlation-Id"

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
)


def _build_telemetry_envelope(encrypted_text: str, is_batch: bool) -> dict:
    """外层 JSON 伪装成常见客户端心跳/同步上报（密文在 sync.cursor，字段名不显式含 encrypt）。"""
    now = int(time.time())
    return {
        "app": {
            "build": "131.0.2903.112",
            "channel": "stable",
            "name": "MsEdgeWebView",
            "v": "131.0.2903.112",
        },
        "device": {
            "id": TELEMETRY_DEVICE_ID,
            "locale": "zh-CN",
            "platform": "windows",
        },
        "evt": {
            "name": "component.sync" if is_batch else "service.heartbeat",
            "t": now,
        },
        "stats": {
            "idle_pct": 70 + (now % 18),
            "mem_avail_mb": 6000 + (now % 1200),
        },
        "sync": {
            "channel": "automatic",
            "cursor": encrypted_text,
            "phase": "delta" if is_batch else "ping",
        },
    }


def to_node_alias(raw):
    text = str(raw or '').strip().upper()
    if not text:
        return '--'

    h = 0
    for ch in text:
        h = ((h << 5) - h) + ord(ch)
        h &= 0xFFFFFFFF

    return f"NODE-{h:08X}"[-13:]


def get_detail_key(item):
    return str(item.get('signal_id') or item.get('id') or item.get('job_id') or '')


def _signal_id_sort_int(item):
    """列表排序：优先按数值 signal_id 降序，使新任务落在首页。"""
    if not isinstance(item, dict):
        return 0
    for k in ("signal_id", "id", "job_id"):
        v = item.get(k)
        if v is None:
            continue
        try:
            return int(v)
        except (TypeError, ValueError):
            continue
    return 0


def _cloud_request(method, url, **kwargs):
    """统一云端请求：超时、是否走系统代理与日志。

    旧版 requests 不支持 request(..., trust_env=)，故用 proxies 显式关闭代理以绕过 HTTP_PROXY。
    """
    kwargs.setdefault("timeout", CLOUD_REQUEST_TIMEOUT)
    if not CLOUD_TRUST_ENV_PROXY:
        kwargs.setdefault("proxies", {"http": None, "https": None})
    LOG.debug(
        "cloud %s %s use_proxy_env=%s timeout=%s",
        method,
        url,
        CLOUD_TRUST_ENV_PROXY,
        kwargs.get("timeout"),
    )
    if method.upper() == "GET":
        return requests.get(url, **kwargs)
    return requests.post(url, **kwargs)


def send_encrypted_signal_to_cloud(real_data, is_batch=False):
    """
    加密真实数据，并发送到云端
    real_data: dict (单笔) 或 list (批量)
    """
    timestamp = str(int(time.time()))
    nonce = str(int(time.time() * 1000))
    
    # 1. 序列化真实的交易指令
    real_body_text = json.dumps(real_data, sort_keys=True, separators=(',', ':'))
    
    # 2. 进行 AES 加密
    encrypted_bytes = CIPHER_SUITE.encrypt(real_body_text.encode('utf-8'))
    encrypted_text = encrypted_bytes.decode('utf-8')
    
    # 3. 构造遥测外形 Payload（明文侧无股票代码；敏感内容仅在 Fernet 密文中）
    safe_payload = _build_telemetry_envelope(encrypted_text, is_batch)
    
    # 4. 对整段 JSON 计算签名（与云端 stable_json_body 一致）
    body_text = json.dumps(
        safe_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    body_hash = hashlib.sha256(body_text.encode("utf-8")).hexdigest()
    
    method = "POST"
    path = PATH_TELEMETRY_SYNC if is_batch else PATH_TELEMETRY_BEAT
    sign_message = f"{method}\n{path}\n{timestamp}\n{nonce}\n{body_hash}"
    
    signature = hmac.new(
        API_SECRET.encode("utf-8"),
        sign_message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    
    headers = {
        _HDR_INSTALL_ID: API_KEY,
        _HDR_BODY_SIG: signature,
        _HDR_REQ_TS: timestamp,
        _HDR_CORRELATION: nonce,
        "Content-Type": "application/json",
        "User-Agent": _BROWSER_UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Origin": "https://ntp.msn.com",
        "Referer": "https://ntp.msn.com/edge/windows",
    }
    
    url = CLOUD_API_BASE + path
    try:
        r = _cloud_request("POST", url, data=body_text.encode("utf-8"), headers=headers)
        LOG.info("telemetry POST %s -> HTTP %s len=%s", path, r.status_code, len(r.content or b""))
        if r.status_code >= 400:
            LOG.warning("telemetry POST body snippet: %s", (r.text or "")[:500])
        try:
            return r.json()
        except Exception as je:
            LOG.error("telemetry POST JSON 解析失败: %s  raw=%s", je, (r.text or "")[:300])
            return {"ok": False, "error": "invalid_json_response"}
    except requests.RequestException as e:
        LOG.error("telemetry POST 失败 %s — %s", url, e)
        LOG.debug("telemetry POST 详情", exc_info=True)
        return {"ok": False, "error": str(e)}
    

# ================= 伪装版前端 HTML =================
# 伪装成企业统一接入 / 会话审计台（浅色办公风，降低与交易 UI 的相似度）
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>统一接入网关 · 会话监察</title>
    <link href="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.0/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdn.bootcdn.net/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <style>
        :root {
            --bg: #f4f6fb;
            --bg-soft: #eef1f8;
            --card: #ffffff;
            --card-border: #e2e8f0;
            --text: #1e293b;
            --muted: #64748b;
            --primary: #0f62fe;
            --primary-2: #0043ce;
            --success: #198038;
            --warning: #b28600;
            --danger: #da1e28;
            --accent: #6929c4;
        }
        body {
            padding: 18px;
            background: var(--bg);
            color: var(--text);
            min-height: 100vh;
            font-family: 'Segoe UI', 'Microsoft YaHei UI', system-ui, sans-serif;
        }
        .card {
            background: var(--card);
            border: 1px solid var(--card-border);
            margin-bottom: 16px;
            border-radius: 8px;
            box-shadow: 0 1px 3px rgba(15, 23, 42, 0.06);
            transition: box-shadow .2s ease;
        }
        .card:hover {
            box-shadow: 0 4px 14px rgba(15, 23, 42, 0.08);
        }
        .card-header {
            background: #f8fafc;
            border-bottom: 1px solid var(--card-border);
            border-radius: 8px 8px 0 0 !important;
            padding: .85rem 1rem;
            font-weight: 600;
            color: #0f172a;
        }
        .table { color: var(--text); }
        .table-dark {
            --bs-table-bg: #f1f5f9;
            --bs-table-border-color: #e2e8f0;
            color: #334155;
        }
        .table-hover tbody tr:hover {
            background-color: #eff6ff;
            color: #0f172a;
        }
        .table > :not(caption) > * > * {
            background-color: #fff;
            color: #334155;
            border-bottom-color: #e2e8f0;
        }
        .table tbody tr:nth-child(even) td {
            background-color: #fafbfc;
        }
        .row-clickable {
            cursor: pointer;
        }
        .detail-code {
            font-family: ui-monospace, monospace;
            color: #0f172a;
            background: #f1f5f9;
            border: 1px solid #cbd5e1;
            border-radius: 6px;
            padding: .35rem .5rem;
            display: inline-block;
        }
        .form-control, .form-select {
            background: #fff;
            border: 1px solid #cbd5e1;
            color: #0f172a;
            border-radius: 6px;
            padding: .6rem .85rem;
        }
        .form-control::placeholder { color: #94a3b8; }
        .input-group-text {
            background: #f8fafc;
            border: 1px solid #cbd5e1;
            color: #475569;
        }
        .text-muted {
            color: var(--muted) !important;
        }
        .form-control:focus, .form-select:focus {
            background: #fff;
            color: #0f172a;
            border-color: var(--primary);
            box-shadow: 0 0 0 2px rgba(15, 98, 254, .15);
        }
        .btn {
            border-radius: 6px;
            padding: .55rem 1rem;
            font-weight: 600;
        }
        .btn-primary { background: var(--primary); border-color: var(--primary); }
        .btn-success { background: var(--success); border-color: var(--success); }
        .btn-outline-secondary {
            border: 1px solid #cbd5e1;
            color: #475569;
            background: #fff;
        }
        .btn-outline-secondary:hover { background: #f1f5f9; color: #0f172a; }
        .metric-card { border-left: 4px solid; padding: .9rem; margin-bottom: .85rem; min-height: 102px; }
        .metric-value {
            font-size: 1.75rem;
            font-weight: 700;
            line-height: 1.05;
            color: #0f172a;
        }
        .metric-label { color: #475569; font-size: .82rem; font-weight: 600; }
        .metric-sub { color: #94a3b8; font-size: .76rem; }
        .status-badge {
            padding: .3rem .7rem;
            border-radius: 999px;
            font-size: .74rem;
            font-weight: 600;
            border: 1px solid rgba(255,255,255,.18);
        }
        .loading-overlay {
            position: fixed;
            inset: 0;
            background: rgba(255, 255, 255, .86);
            display: none;
            align-items: center;
            justify-content: center;
            z-index: 9999;
        }
        .spinner {
            width: 44px;
            height: 44px;
            border: 3px solid #e2e8f0;
            border-top: 3px solid var(--primary);
            border-radius: 50%;
            animation: spin 1s linear infinite;
        }
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
        .toast {
            position: fixed;
            bottom: 16px;
            right: 16px;
            z-index: 999;
            min-width: 310px;
            border-radius: 12px;
            overflow: hidden;
        }
        .navbar {
            background: #fff;
            border: 1px solid var(--card-border);
            border-radius: 8px;
            padding: .85rem 1rem;
            margin-bottom: 1rem;
            box-shadow: 0 1px 3px rgba(15, 23, 42, 0.06);
        }
        .cluster-status { display: flex; align-items: center; gap: 9px; font-size: .88rem; color: #64748b; }
        .cluster-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--success);
        }
        @keyframes pulse {
            0% { transform: scale(1); box-shadow: 0 0 0 0 rgba(31,191,117,.55); }
            70% { transform: scale(1.05); box-shadow: 0 0 0 11px rgba(31,191,117,0); }
            100% { transform: scale(1); box-shadow: 0 0 0 0 rgba(31,191,117,0); }
        }
        .info-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: .55rem;
        }
        .info-item {
            background: #f8fafc;
            border: 1px solid #e2e8f0;
            border-radius: 6px;
            padding: .55rem .65rem;
        }
        .info-item .k { color: #64748b; font-size: .74rem; }
        .info-item .v { color: #0f172a; font-size: .86rem; font-weight: 600; }
        .activity-log {
            background: #f8fafc;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: .85rem;
        }
        .activity-log .entry {
            border-left: 3px solid #0f62fe;
            padding-left: .75rem;
            margin-bottom: .6rem;
        }
        .activity-log small {
            color: #475569 !important;
            font-weight: 500;
        }
        .badge-soft {
            display: inline-block;
            background: #e0e7ff;
            color: #3730a3;
            border: 1px solid #c7d2fe;
            border-radius: 999px;
            font-size: .72rem;
            padding: .16rem .5rem;
        }
        .table-footer-text {
            color: #475569;
            font-weight: 600;
        }
        .event-tag {
            display: inline-block;
            padding: .16rem .5rem;
            border-radius: 4px;
            font-size: .72rem;
            color: #334155;
            background: #e2e8f0;
            border: 1px solid #cbd5e1;
            max-width: 150px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            vertical-align: middle;
        }
        .form-check-label {
            color: #334155;
            font-weight: 600;
        }
        .card-header, .card-header span, .card-header i {
            color: #0f172a;
        }
        .card-body label {
            color: #334155;
            font-weight: 600;
        }
        .activity-log .status-badge {
            color: #fff;
            font-weight: 600;
        }
    </style>
</head>
<body>
    <div class="loading-overlay" id="loadingOverlay">
        <div class="spinner"></div>
        <div class="ms-3" style="color: #334155; font-weight: 600;">正在与接入平面同步会话索引…</div>
    </div>

    <div class="toast" id="successToast" style="display: none;">
        <div class="card bg-success text-white border-0">
            <div class="card-body p-3">
                <div class="d-flex align-items-center">
                    <i class="fas fa-check-circle fa-lg me-3"></i>
                    <div>
                        <h6 class="mb-0">已受理</h6>
                        <small id="toastMessage">请求已写入接入平面</small>
                    </div>
                    <button type="button" class="btn-close btn-close-white ms-auto" onclick="hideToast('successToast')"></button>
                </div>
            </div>
        </div>
    </div>

    <div class="toast" id="errorToast" style="display: none;">
        <div class="card bg-danger text-white border-0">
            <div class="card-body p-3">
                <div class="d-flex align-items-center">
                    <i class="fas fa-exclamation-triangle fa-lg me-3"></i>
                    <div>
                        <h6 class="mb-0">未受理</h6>
                        <small id="errorToastMessage">请求未能通过校验或上游不可用</small>
                    </div>
                    <button type="button" class="btn-close btn-close-white ms-auto" onclick="hideToast('errorToast')"></button>
                </div>
            </div>
        </div>
    </div>

    <nav class="navbar navbar-expand-lg">
        <div class="container">
            <a class="navbar-brand d-flex align-items-center" href="#">
                <i class="fas fa-network-wired fa-xl me-2" style="color: #0f62fe;"></i>
                <div>
                    <h4 class="mb-0" style="color:#0f172a;">统一接入网关 · 会话监察</h4>
                    <div class="cluster-status">
                        <div class="cluster-dot"></div>
                        <span>平面状态：<strong>运行中</strong> ｜ 对等链路：24 ｜ 策略版本：2026.04</span>
                    </div>
                </div>
            </a>
            <div class="d-flex align-items-center">
                <div class="me-3">
                    <small class="text-muted">最近拉取：<span id="lastSyncTime">--:--:--</span></small>
                </div>
                <button class="btn btn-outline-secondary" onclick="refreshList()">
                    <i class="fas fa-sync-alt me-2"></i>刷新索引
                </button>
            </div>
        </div>
    </nav>

    <div class="modal fade" id="taskDetailModal" tabindex="-1" aria-hidden="true">
      <div class="modal-dialog modal-lg modal-dialog-centered">
        <div class="modal-content" style="background:#fff; border:1px solid #e2e8f0; color:#334155; border-radius:8px;">
          <div class="modal-header" style="border-bottom:1px solid #e2e8f0;">
            <h5 class="modal-title"><i class="fas fa-file-lines me-2"></i>会话审计明细</h5>
            <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
          </div>
          <div class="modal-body">
            <div class="row g-3">
              <div class="col-md-6"><small class="text-muted d-block">会话流水号</small><div id="detailJobId">-</div></div>
              <div class="col-md-6"><small class="text-muted d-block">端点指纹</small><div id="detailNodeAlias">-</div></div>
              <div class="col-md-6"><small class="text-muted d-block">敏感标识（口令后展示）</small><div id="detailRawCode" class="detail-code">—</div></div>
              <div class="col-md-6"><small class="text-muted d-block">会话状态</small><div id="detailStatus">-</div></div>
              <div class="col-md-6"><small class="text-muted d-block">策略向</small><div id="detailAction">-</div></div>
              <div class="col-md-6"><small class="text-muted d-block">路由标签</small><div id="detailTag">-</div></div>
              <div class="col-md-6"><small class="text-muted d-block">会话模板</small><div id="detailSignalType">-</div></div>
              <div class="col-md-6"><small class="text-muted d-block">定价模式</small><div id="detailPriceType">-</div></div>
              <div class="col-md-6"><small class="text-muted d-block">预约阈值</small><div id="detailLimitPx">-</div></div>
              <div class="col-12"><small class="text-muted d-block">记录时间</small><div id="detailTime">-</div></div>
              <div class="col-md-6"><small class="text-muted d-block">面板查询(ui_panel)</small><div id="detailUiPanel" style="font-family:ui-monospace,monospace;">—</div></div>
              <div class="col-12"><small class="text-muted d-block">面板 OCR 全文（UI_READ 完成后）</small><pre id="detailUiOcr" class="mb-0" style="white-space:pre-wrap;font-size:12px;max-height:240px;overflow:auto;background:#f8fafc;padding:8px;border-radius:6px;border:1px solid #e2e8f0;">—</pre></div>
            </div>
          </div>
        </div>
      </div>
    </div>

<div class="container">
    <div class="row mb-4">
        <div class="col-md-3">
            <div class="card metric-card" style="border-left-color: #3b82f6;">
                <div class="metric-value" id="activeJobs">0</div>
                <div class="metric-label">索引会话数</div>
                <div class="metric-sub">待处理 + 已关闭 + 异常</div>
            </div>
        </div>
        <div class="col-md-3">
            <div class="card metric-card" style="border-left-color: #10b981;">
                <div class="metric-value" id="successRate">100%</div>
                <div class="metric-label">会话成功率</div>
                <div class="metric-sub">成功 / 总会话（实时）</div>
            </div>
        </div>
        <div class="col-md-3">
            <div class="card metric-card" style="border-left-color: #f59e0b;">
                <div class="metric-value" id="avgLatency">0ms</div>
                <div class="metric-label">平均往返时延</div>
                <div class="metric-sub">本机到接入平面估算</div>
            </div>
        </div>
        <div class="col-md-3">
            <div class="card metric-card" style="border-left-color: #8b5cf6;">
                <div class="metric-value" id="cpuUtil">--%</div>
                <div class="metric-label">本机负载</div>
                <div class="metric-sub">监察端进程估算</div>
            </div>
        </div>
    </div>
    
    <div class="row">
        <div class="col-lg-5">
            <div class="card shadow-sm">
                <div class="card-header d-flex align-items-center justify-content-between">
                    <span><i class="fas fa-sliders me-2"></i>策略下发</span>
                    <span class="badge bg-secondary">人工受理</span>
                </div>
                <div class="card-body">
                    <div class="mb-4">
                        <h5 class="mb-3" style="color:#0f172a;"><i class="fas fa-fingerprint me-2"></i>接入参数</h5>
                        <div id="standardDispatchFields">
                        <div class="mb-3">
                            <label class="form-label mb-2">接入主体指纹</label>
                            <input type="text" id="target_hash" class="form-control" placeholder="例如 EP-7A2F 或内部登记号" style="font-family: ui-monospace, monospace;">
                            <small class="text-muted">用于绑定对端会话，展示名由平面侧哈希生成</small>
                        </div>
                        
                        <div class="row g-3 mb-3">
                            <div class="col-md-6">
                                <label class="form-label mb-2">策略向</label>
                                <select id="operation" class="form-select">
                                    <option value="START">扩容（正向会话）</option>
                                    <option value="TERMINATE">缩容（反向会话）</option>
                                </select>
                                <small class="text-muted d-block mt-1">U1 必选；U2 若填写下方「目标仓位%」则必选（表示买/卖方向）</small>
                            </div>
                            <div class="col-md-6">
                                <label class="form-label mb-2">并发会话上限</label>
                                <div class="input-group">
                                    <input type="number" id="threads" class="form-control" value="100" min="1" max="1000">
                                    <span class="input-group-text">路</span>
                                </div>
                                <small class="text-muted">取值范围 1–1000</small>
                            </div>
                        </div>
                        </div>
                        <div class="row g-3 mb-3">
                            <div class="col-md-6">
                                <label class="form-label mb-2">会话模板</label>
                                <select id="signal_profile" class="form-select">
                                    <option value="ORDER">U1 · 带向会话</option>
                                    <option value="TARGET">U2 · 目标位会话</option>
                                    <option value="UI_READ">U0 · 柜台面板 OCR</option>
                                </select>
                            </div>
                            <div class="col-md-6" id="quoteStyleCol">
                                <label class="form-label mb-2">定价模式</label>
                                <select id="quote_style" class="form-select" onchange="toggleLimitRow()">
                                    <option value="MARKET">T1 · 即时</option>
                                    <option value="LIMIT">T2 · 预约阈值</option>
                                </select>
                            </div>
                        </div>
                        <div id="uiReadDispatchFields" class="mb-3 border rounded p-3" style="display:none;background:#f8fafc;border-color:#e2e8f0!important;">
                            <label class="form-label mb-2"><i class="fas fa-camera me-1"></i>读取底栏面板（委托 / 成交 / 资金 / 持仓）</label>
                            <select id="ui_read_panel" class="form-select mb-2">
                                <option value="orders">委托</option>
                                <option value="trades">成交</option>
                                <option value="funds">资金</option>
                                <option value="positions">持仓</option>
                            </select>
                            <div class="d-flex flex-wrap gap-2 mb-2">
                                <button type="button" class="btn btn-sm btn-outline-primary" onclick="submitUiRead('orders')">仅发：委托</button>
                                <button type="button" class="btn btn-sm btn-outline-primary" onclick="submitUiRead('trades')">仅发：成交</button>
                                <button type="button" class="btn btn-sm btn-outline-primary" onclick="submitUiRead('funds')">仅发：资金</button>
                                <button type="button" class="btn btn-sm btn-outline-primary" onclick="submitUiRead('positions')">仅发：持仓</button>
                            </div>
                            <small class="text-muted">与下方「提交」相同：经 18080 写入接入平面后，由<strong>运行 EMS 的机器</strong>唤起交易客户端、点底栏 Tab；其中「持仓」对 <code>RPA_CONFIG.bbox_positions_table</code> 区域截图并 OCR，文本回写库后由本页与会话索引展示（与查询持仓股数用的是同一块表区域）。</small>
                            <small class="text-danger d-block mt-2" style="font-size:0.8rem;">① 仍提示旧句「TARGET 或 ORDER」= 18080 上跑的仍是旧 <code>signal_ingest</code> 或连错实例；请访问云 <code>/health</code> 看 <code>signal_schema_version</code> 是否为 <strong>2</strong>。② 部署新版 <code>api_server</code> 后，同类校验失败会在句末附带 <code>| ingest_file=...</code> 路径，按路径核对服务器上到底是不是你改的那份文件。③ 若暂时修不好云接口：在本机能连 MySQL 的前提下，启动指挥部前设环境变量 <code>EMS_UI_READ_INSERT_MODE=direct</code>，并保证 <code>src/config.py</code> 里 <code>DB_CONFIG</code> 指向同一库，则 U0 面板查询会<strong>绕过云</strong>直接 <code>insert_signal</code>。</small>
                        </div>
                        <div id="uiReadResultCard" class="card border-primary shadow-sm mt-3 mb-2" style="display:none;">
                            <div class="card-header py-2 d-flex justify-content-between align-items-center flex-wrap gap-2">
                                <span><i class="fas fa-file-alt me-2"></i>U0 查询结果（OCR 全文）</span>
                                <div class="d-flex gap-1">
                                    <button type="button" class="btn btn-sm btn-outline-primary" onclick="copyUiReadOcr()" title="复制全文">复制</button>
                                    <button type="button" class="btn btn-sm btn-outline-secondary" onclick="stopUiReadPoll(); document.getElementById('uiReadResultCard').style.display='none';">收起</button>
                                </div>
                            </div>
                            <div class="card-body py-2">
                                <div class="d-flex flex-wrap gap-3 small mb-2">
                                    <div><span class="text-muted">流水号</span> <code id="uiReadResSid">—</code></div>
                                    <div><span class="text-muted">面板</span> <span id="uiReadResPanel">—</span></div>
                                    <div><span class="text-muted">状态</span> <span id="uiReadResStatus" class="badge bg-secondary">—</span></div>
                                </div>
                                <div id="uiReadResErr" class="alert alert-danger py-1 px-2 small mb-2" style="display:none;"></div>
                                <div id="uiReadDiagHint" class="alert alert-info py-2 small mb-2" style="display:none;white-space:pre-wrap;word-break:break-word;line-height:1.45;"></div>
                                <label class="form-label small text-muted mb-1">识别全文</label>
                                <pre id="uiReadResOcr" class="rounded border bg-light p-2 mb-0" style="max-height:360px;overflow:auto;white-space:pre-wrap;word-break:break-word;font-size:0.78rem;line-height:1.35;">（下发 U0 后将显示 OCR；右侧表「OCR/摘要」为截断预览）</pre>
                                <small class="text-muted d-block mt-2">下发成功后每 2.5s 自动拉取列表中的本条状态（与「定时刷新」共用同一接口）。<strong>截图与 OCR 在装交易软件并运行 EMS 的那台电脑执行</strong>，本页只读库里的结果。也可点击右侧会话索引中对应行同步到此处。若需更多后台日志：运行指挥部的终端会打 U0 受理行；设环境变量 <code>EMS_COMMANDER_LOG_DEBUG=1</code> 可打开 DEBUG（含每次列表拉取摘要）。</small>
                            </div>
                        </div>
                        <div class="mb-3" id="limitPriceRow" style="display: none;">
                            <label class="form-label mb-2">预约阈值</label>
                            <input type="number" id="limit_anchor" class="form-control" step="0.001" min="0" placeholder="数值 &gt; 0">
                            <small class="text-muted">选用 T2 时必填，由接入平面落库解析</small>
                        </div>
                        <div class="mb-3" id="u2WeightPctRow" style="display: none;">
                            <label class="form-label mb-2">目标仓位 %（仅 U2 选填）</label>
                            <input type="number" id="u2_weight_pct" class="form-control" step="0.01" min="0" max="100" placeholder="留空则仍按「目标股数差」调仓（读持仓）">
                            <small class="text-muted">填写 1–100 时下发 quantity_mode=TARGET_PCT，RPA 走「持仓比例」；须同时选左侧扩容/缩容表示买或卖</small>
                        </div>
                        
                        <button class="btn btn-primary w-100 py-2" onclick="doOrder()">
                            <i class="fas fa-paper-plane me-2"></i>提交至接入平面
                        </button>
                    </div>
                    
                    <hr style="border-color:#e2e8f0;">
                    
                    <div>
                        <h5 class="mb-3" style="color:#0f172a;"><i class="fas fa-table me-2"></i>批量导入</h5>
                        <div class="mb-3">
                            <label class="form-label mb-2">选择 CSV</label>
                            <div class="input-group">
                                <input type="file" id="csvFile" class="form-control" accept=".csv">
                                <button class="btn btn-outline-secondary" type="button" onclick="document.getElementById('csvFile').click()">
                                    <i class="fas fa-folder-open"></i>
                                </button>
                            </div>
                            <small class="text-muted">单文件 ≤10MB；列名支持 code/vol、target_hash/threads、operation、signal_type、price_type、limit_price、quantity_mode、weight_pct、action 等。</small>
                        </div>
                        <button class="btn btn-success w-100 py-2" onclick="uploadCSV()">
                            <i class="fas fa-cloud-upload-alt me-2"></i>上传并下发
                        </button>
                    </div>
                </div>
            </div>
            
            <div class="card shadow-sm mt-4">
                <div class="card-header">
                    <i class="fas fa-chart-line me-2"></i>平面健康度（演示）
                </div>
                <div class="card-body">
                    <div class="d-flex justify-content-between align-items-center mb-3">
                        <small>链路负载</small>
                        <small class="text-success"><i class="fas fa-arrow-up me-1"></i><span id="clusterLoadText">65%</span></small>
                    </div>
                    <div class="progress mb-4" style="height: 8px; background: #e2e8f0;">
                        <div class="progress-bar bg-success" id="clusterLoadBar" style="width: 65%"></div>
                    </div>
                    
                    <div class="d-flex justify-content-between align-items-center mb-3">
                        <small>网络吞吐</small>
                        <small id="networkThroughputText">1.2 Gb/s</small>
                    </div>
                    <div class="progress mb-4" style="height: 8px; background: #e2e8f0;">
                        <div class="progress-bar bg-info" id="networkThroughputBar" style="width: 80%"></div>
                    </div>
                    
                    <div class="d-flex justify-content-between align-items-center mb-3">
                        <small>存储 IOPS</small>
                        <small id="storageIopsText">45k</small>
                    </div>
                    <div class="progress" style="height: 8px; background: #e2e8f0;">
                        <div class="progress-bar bg-warning" id="storageIopsBar" style="width: 45%"></div>
                    </div>
                </div>
            </div>

            <div class="card shadow-sm mt-4">
                <div class="card-header d-flex justify-content-between align-items-center">
                    <span><i class="fas fa-circle-info me-2"></i>接入说明</span>
                    <span class="badge-soft">只读</span>
                </div>
                <div class="card-body">
                    <div class="info-grid">
                        <div class="info-item"><div class="k">北向接口族</div><div class="v">/v1/agent/telemetry/*</div></div>
                        <div class="info-item"><div class="k">传输</div><div class="v">HTTPS + 签名头</div></div>
                        <div class="info-item"><div class="k">载荷</div><div class="v">同步游标内嵌密文</div></div>
                        <div class="info-item"><div class="k">索引刷新</div><div class="v">手动 / 5s 轮询</div></div>
                        <div class="info-item"><div class="k">批量</div><div class="v">CSV</div></div>
                        <div class="info-item"><div class="k">审计</div><div class="v">会话级</div></div>
                    </div>
                </div>
            </div>
        </div>

        <div class="col-lg-7">
            <div class="card shadow-sm">
                <div class="card-header d-flex align-items-center justify-content-between">
                    <span><i class="fas fa-list-ul me-2"></i>会话索引</span>
                    <div class="d-flex align-items-center">
                        <div class="form-check form-switch me-3">
                            <input class="form-check-input" type="checkbox" id="autoRefresh" checked onchange="toggleAutoRefresh()">
                            <label class="form-check-label" for="autoRefresh">定时刷新</label>
                        </div>
                        <button class="btn btn-sm btn-outline-secondary" onclick="refreshList()">
                            <i class="fas fa-redo me-1"></i>刷新
                        </button>
                    </div>
                </div>
                <div class="card-body p-0">
                    <div class="table-responsive" style="max-height: 500px;">
                        <table class="table table-hover mb-0">
                            <thead class="table-dark">
                                <tr>
                                    <th style="width: 11%;">流水号</th>
                                    <th style="width: 14%;">端点指纹</th>
                                    <th style="width: 11%;">策略向</th>
                                    <th style="width: 10%;">会话状态</th>
                                    <th style="width: 22%;">OCR/摘要</th>
                                    <th style="width: 10%;">时间</th>
                                    <th style="width: 10%;">路由标签</th>
                                    <th style="width: 10%;">配置层</th>
                                </tr>
                            </thead>
                            <tbody id="signalList">
                                <tr>
                                    <td colspan="8" class="text-center py-5">
                                        <div class="text-muted">
                                            <i class="fas fa-inbox fa-2x mb-3"></i>
                                            <p>暂无会话记录，可先提交一条策略。</p>
                                        </div>
                                    </td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>
                <div class="card-footer">
                    <div class="d-flex justify-content-between align-items-center flex-wrap gap-2">
                        <small class="table-footer-text">页 <span id="currentPage">1</span>/<span id="totalPages">1</span> ｜ 总 <span id="totalCount">0</span> 条 ｜ 本页 <span id="jobCount">0</span> 条</small>
                        <div class="d-flex align-items-center gap-2">
                            <div class="btn-group">
                                <button class="btn btn-sm btn-outline-secondary" onclick="filterJobs('all', event)">全部</button>
                                <button class="btn btn-sm btn-outline-secondary" onclick="filterJobs('pending', event)">待处理</button>
                                <button class="btn btn-sm btn-outline-secondary" onclick="filterJobs('success', event)">成功</button>
                                <button class="btn btn-sm btn-outline-secondary" onclick="filterJobs('error', event)">异常</button>
                            </div>
                            <select id="pageSize" class="form-select form-select-sm" style="width: 96px;" onchange="changePageSize()">
                                <option value="10">10/页</option>
                                <option value="20" selected>20/页</option>
                                <option value="50">50/页</option>
                                <option value="100">100/页</option>
                            </select>
                            <button class="btn btn-sm btn-outline-secondary" onclick="prevPage()">上一页</button>
                            <button class="btn btn-sm btn-outline-secondary" onclick="nextPage()">下一页</button>
                        </div>
                    </div>
                </div>
            </div>
            
            <div class="card shadow-sm mt-4">
                <div class="card-header">
                    <i class="fas fa-history me-2"></i>平面事件（示例）
                </div>
                <div class="card-body">
                    <div class="activity-log" style="max-height: 200px; overflow-y: auto;">
                        <div class="d-flex align-items-start mb-3">
                            <div class="status-badge bg-success me-3">INFO</div>
                            <div class="entry">
                                <small class="d-block text-muted">刚刚</small>
                                <small>监察台已就绪，策略通道握手正常。</small>
                            </div>
                        </div>
                        <div class="d-flex align-items-start mb-3">
                            <div class="status-badge bg-info me-3">DEBUG</div>
                            <div class="entry">
                                <small class="d-block text-muted">2 分钟前</small>
                                <small>对等探活完成，24/24 链路可达。</small>
                            </div>
                        </div>
                        <div class="d-flex align-items-start">
                            <div class="status-badge bg-warning me-3">WARN</div>
                            <div class="entry">
                                <small class="d-block text-muted">15 分钟前</small>
                                <small>区域 R3 出现短时抖动，已自愈。</small>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
</div>

<script>
let autoRefreshInterval = null;
let currentFilter = 'all';
let currentPage = 1;
let pageSize = 20;
let totalPages = 1;
let totalCount = 0;
let lastData = [];
let currentRenderedData = [];
let pendingDetailKey = '';
let uiReadPollTimer = null;
let uiReadTrackedId = null;
let uiReadPollStartedAt = null;
let uiReadPollCount = 0;

let metricState = {
    avgLatency: 26,
    cpuUtil: 52,
    clusterLoad: 65,
    networkThroughput: 1.2,
    storageIops: 45
};

function showLoading() {
    document.getElementById('loadingOverlay').style.display = 'flex';
}

function hideLoading() {
    document.getElementById('loadingOverlay').style.display = 'none';
}

function showToast(message, isSuccess = true) {
    const toastId = isSuccess ? 'successToast' : 'errorToast';
    const toast = document.getElementById(toastId);
    const messageEl = isSuccess ? document.getElementById('toastMessage') : document.getElementById('errorToastMessage');
    
    messageEl.textContent = message;
    toast.style.display = 'block';
    
    setTimeout(() => {
        hideToast(toastId);
    }, 5000);
}

function hideToast(toastId) {
    document.getElementById(toastId).style.display = 'none';
}

function clamp(v, min, max) {
    return Math.max(min, Math.min(max, v));
}

function driftMetric(current, min, max, step) {
    const delta = (Math.random() * 2 - 1) * step;
    return clamp(current + delta, min, max);
}

function updateDynamicHealthMetrics() {
    metricState.avgLatency = driftMetric(metricState.avgLatency, 12, 85, 4.8);
    metricState.cpuUtil = driftMetric(metricState.cpuUtil, 35, 88, 4.2);
    metricState.clusterLoad = driftMetric(metricState.clusterLoad, 42, 93, 3.6);
    metricState.networkThroughput = driftMetric(metricState.networkThroughput, 0.8, 3.6, 0.22);
    metricState.storageIops = driftMetric(metricState.storageIops, 28, 96, 5.1);

    document.getElementById('avgLatency').textContent = `${Math.round(metricState.avgLatency)}ms`;
    document.getElementById('cpuUtil').textContent = `${Math.round(metricState.cpuUtil)}%`;

    const load = Math.round(metricState.clusterLoad);
    document.getElementById('clusterLoadText').textContent = `${load}%`;
    document.getElementById('clusterLoadBar').style.width = `${load}%`;

    const throughput = metricState.networkThroughput.toFixed(1);
    const throughputPct = clamp(Math.round((metricState.networkThroughput / 3.6) * 100), 22, 100);
    document.getElementById('networkThroughputText').textContent = `${throughput} Gb/s`;
    document.getElementById('networkThroughputBar').style.width = `${throughputPct}%`;

    const iopsK = Math.round(metricState.storageIops);
    document.getElementById('storageIopsText').textContent = `${iopsK}k`;
    document.getElementById('storageIopsBar').style.width = `${clamp(iopsK, 20, 100)}%`;
}

function updateMetrics(data, stats = null) {
    const statTotal = stats ? (stats.total || 0) : ((data || []).length);
    const successJobs = stats ? (stats.success || 0) : (data || []).filter(job => job.status === 'SUCCESS' || job.status === 'COMPLETED' || job.status === 'EXECUTED').length;

    document.getElementById('activeJobs').textContent = statTotal;
    document.getElementById('jobCount').textContent = (data || []).length;

    const successRate = statTotal > 0 ? Math.round((successJobs / statTotal) * 100) : 100;
    document.getElementById('successRate').textContent = `${successRate}%`;

    updateDynamicHealthMetrics();
    
    const now = new Date();
    const timeStr = now.toLocaleTimeString('zh-CN', { hour12: false });
    document.getElementById('lastSyncTime').textContent = timeStr;
}

function getStatusBadge(status) {
    switch(status) {
        case 'PENDING':
        case 'WAITING':
            return 'bg-warning text-dark';
        case 'PROCESSING':
            return 'bg-info text-dark';
        case 'SUCCESS':
        case 'COMPLETED':
        case 'EXECUTED':
            return 'bg-success';
        case 'ERROR':
        case 'FAILED':
        case 'REJECTED':
            return 'bg-danger';
        default:
            return 'bg-secondary';
    }
}

function getStatusText(status) {
    const statusMap = {
        'PENDING': '待处理',
        'WAITING': '等待中',
        'PROCESSING': '执行中',
        'SUCCESS': '成功', 
        'COMPLETED': '已完成',
        'EXECUTED': '已执行',
        'ERROR': '异常',
        'FAILED': '失败',
        'REJECTED': '已拒绝'
    };
    return statusMap[status] || status;
}

function formatTimestamp(timestamp) {
    if (!timestamp) return '--:--:--';
    
    try {
        const date = new Date(timestamp);
        if (isNaN(date.getTime())) return '--:--:--';
        
        return date.toLocaleTimeString('zh-CN', { 
            hour12: false,
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit'
        });
    } catch {
        return '--:--:--';
    }
}

function escHtml(str) {
    return String(str || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function setUiReadDiagBox(level, text) {
    const el = document.getElementById('uiReadDiagHint');
    if (!el) return;
    if (!text) {
        el.style.display = 'none';
        el.textContent = '';
        return;
    }
    const base = 'alert py-2 small mb-2';
    if (level === 'danger') el.className = base + ' alert-danger';
    else if (level === 'warning') el.className = base + ' alert-warning';
    else el.className = base + ' alert-info';
    el.style.whiteSpace = 'pre-wrap';
    el.style.display = 'block';
    el.textContent = text;
}

function refreshUiReadDiagnosis(row) {
    const el = document.getElementById('uiReadDiagHint');
    if (!el || !uiReadTrackedId) return;
    const sid = String(uiReadTrackedId);
    const elapsed = uiReadPollStartedAt != null
        ? Math.max(0, Math.floor((Date.now() - uiReadPollStartedAt) / 1000))
        : 0;
    const st = row && row.status ? String(row.status).toUpperCase() : 'UNKNOWN';
    const lines = [
        '【说明】本页不跑 RPA：截图、OCR 在「运行 EMS + 柜台客户端」的机器上进行；此处仅同步数据库中的状态与文本。',
        '流水号 ' + sid + ' · 状态 ' + getStatusText(st) + ' · 已轮询 ' + uiReadPollCount + ' 次 · 已等待约 ' + elapsed + ' s。',
    ];
    if (st === 'PENDING' || st === 'WAITING') {
        lines.push('待处理含义：EMS 尚未把该条置为 SUCCESS，或未执行完。请到交易机查看 python main（或 EMS 服务）是否在跑，并查 MySQL trade_signals 中本条 status / last_error。');
        if (elapsed >= 25) lines.push('若超过约半分钟仍为待处理：多半是 EMS 未连接同一数据库，或 Cloud API 未把信号写入 EMS 所用的库。');
    } else if (st === 'PROCESSING') {
        lines.push('执行中：通常表示 EMS 已领取任务，正在进行切 Tab / 截图 / OCR。');
    } else if (st === 'SUCCESS') {
        const raw = row && row.ui_ocr_text != null ? String(row.ui_ocr_text).trim() : '';
        if (!raw) lines.push('已成功但 OCR 为空：可能被裁区无字或识别失败，请到交易机查 EMS / Tesseract 日志。');
        else lines.push('识别文本已回填；若需审计全文可点会话明细（口令）。');
    }
    setUiReadDiagBox('info', lines.join('\\n'));
}

function fillUiReadResultCard(row) {
    if (!row) return;
    const sid = row.signal_id != null ? String(row.signal_id) : '—';
    document.getElementById('uiReadResSid').textContent = sid;
    document.getElementById('uiReadResPanel').textContent = row.ui_panel || '—';
    const st = (row.status || 'UNKNOWN').toUpperCase();
    const stEl = document.getElementById('uiReadResStatus');
    stEl.textContent = getStatusText(st);
    stEl.className = 'badge status-badge ' + getStatusBadge(st);
    const errEl = document.getElementById('uiReadResErr');
    const err = (row.last_error || '').trim();
    if (err) {
        errEl.style.display = 'block';
        errEl.textContent = err;
    } else {
        errEl.style.display = 'none';
        errEl.textContent = '';
    }
    const ocr = row.ui_ocr_text != null ? String(row.ui_ocr_text) : '';
    const pre = document.getElementById('uiReadResOcr');
    if (ocr.trim()) {
        pre.textContent = ocr;
    } else {
        pre.textContent = '（暂无识别文本；待处理时请等待 EMS 执行；若失败请查看上方错误说明）';
    }
    refreshUiReadDiagnosis(row);
}

function stopUiReadPoll() {
    if (uiReadPollTimer) {
        clearInterval(uiReadPollTimer);
        uiReadPollTimer = null;
    }
}

function pollUiReadOnce() {
    if (!uiReadTrackedId) return;
    uiReadPollCount += 1;
    fetch('/list?page=1&page_size=500&filter=all')
        .then(function (r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        })
        .then(function (payload) {
            const meta = payload.meta || {};
            if (meta.upstream_ok === false) {
                setUiReadDiagBox(
                    'warning',
                    '列表接口：接入平面不可用 — ' + (meta.upstream_msg || '未知') + '\\n请检查 CLOUD_API_BASE / 18080 与本机网络。'
                );
                console.warn('[ems U0 poll] upstream fail', meta);
                return;
            }
            const items = payload.items || [];
            const row = items.find(function (it) { return String(it.signal_id) === String(uiReadTrackedId); });
            if (!row) {
                const total = payload.total != null ? payload.total : items.length;
                setUiReadDiagBox(
                    'warning',
                    '本轮列表第 1 页未找到流水号 ' + uiReadTrackedId + '（本页 ' + items.length + ' 条，过滤后合计约 ' + total + ' 条）。\\n请点击右侧会话索引中含该流水号的一行以同步；若仍没有，确认接入平面返回里是否包含该 signal_id。'
                );
                console.warn('[ems U0 poll] sid not in page', uiReadTrackedId, 'items_len=', items.length);
                return;
            }
            fillUiReadResultCard(row);
            const st = (row.status || '').toUpperCase();
            const hasOcr = (row.ui_ocr_text || '').trim().length > 0;
            if (st === 'SUCCESS') stopUiReadPoll();
            if (['FAILED', 'ERROR', 'REJECTED'].indexOf(st) >= 0) stopUiReadPoll();
            if (st === 'SUCCESS' && !hasOcr) console.info('[ems U0 poll] SUCCESS but empty OCR');
        })
        .catch(function (err) {
            var msg = err && err.message ? err.message : String(err);
            setUiReadDiagBox('danger', '轮询 /list 失败：' + msg + '\\n请打开浏览器控制台 (F12) 查看 Network。');
            console.error('[ems U0 poll]', err);
        });
}

function startUiReadPoll() {
    stopUiReadPoll();
    if (!uiReadTrackedId) return;
    uiReadPollStartedAt = Date.now();
    uiReadPollCount = 0;
    pollUiReadOnce();
    uiReadPollTimer = setInterval(pollUiReadOnce, 2500);
}

function syncUiReadPanelFromList() {
    if (!uiReadTrackedId || !lastData || !lastData.length) return;
    const row = lastData.find(function (it) { return String(it.signal_id) === String(uiReadTrackedId); });
    if (row) fillUiReadResultCard(row);
}

function copyUiReadOcr() {
    const pre = document.getElementById('uiReadResOcr');
    const s = pre ? pre.textContent : '';
    if (!s || s.indexOf('（暂无识别文本') === 0 || s.indexOf('（暂无识别') === 0) {
        showToast('暂无可复制的 OCR 内容', false);
        return;
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(s).then(function () {
            showToast('已复制 OCR 全文');
        }).catch(function () {
            showToast('复制失败', false);
        });
    } else {
        showToast('浏览器不支持剪贴板 API', false);
    }
}

function toNodeAlias(raw) {
    const text = String(raw || '').trim().toUpperCase();
    if (!text) return '--';

    let hash = 0;
    for (let i = 0; i < text.length; i++) {
        hash = ((hash << 5) - hash) + text.charCodeAt(i);
        hash |= 0;
    }

    const hex = Math.abs(hash).toString(16).toUpperCase().padStart(6, '0').slice(0, 6);
    return `NODE-${hex}`;
}

function refreshList(silent) {
    var quiet = silent === true;
    if (!quiet) showLoading();

    const params = new URLSearchParams({
        page: String(currentPage),
        page_size: String(pageSize),
        filter: currentFilter
    });

    fetch(`/list?${params.toString()}`)
        .then(function (res) {
            if (!res.ok) throw new Error('/list HTTP ' + res.status);
            return res.json();
        })
        .then(function (payload) {
            const data = payload.items || [];
            const meta = payload.meta || {};
            lastData = data;
            currentPage = payload.page || 1;
            pageSize = payload.page_size || pageSize;
            totalPages = payload.total_pages || 1;
            totalCount = payload.total || data.length;

            updateMetrics(data, payload.stats || null);
            if (meta.upstream_ok === false) {
                document.getElementById('lastSyncTime').textContent =
                    '失败 ' + (meta.upstream_msg || '');
                if (!quiet) {
                    showToast('接入平面同步失败：' + (meta.upstream_msg || '未知'), false);
                } else {
                    console.warn('[ems] 后台同步失败', meta.upstream_msg);
                }
            }
            renderTable(data);
            updatePaginationUI();
            syncUiReadPanelFromList();
            if (!quiet) hideLoading();
        })
        .catch(function (err) {
            console.error('Sync failed:', err);
            document.getElementById('lastSyncTime').textContent = '失败 ' + (err.message || '');
            if (!quiet) {
                showToast('索引拉取失败：' + (err.message || '无法连接'), false);
                hideLoading();
            }
        });
}

function renderTable(data) {
    const tbody = document.getElementById('signalList');
    
    if (!data || data.length === 0) {
        currentRenderedData = [];
        tbody.innerHTML = `
            <tr>
                <td colspan="8" class="text-center py-5">
                    <div class="text-muted">
                        <i class="fas fa-inbox fa-2x mb-3"></i>
                        <p>暂无任务记录，可先发起一次调度。</p>
                    </div>
                </td>
            </tr>`;
        return;
    }
    
    // 应用过滤
    let filteredData = data;
    if (currentFilter === 'pending') {
        filteredData = data.filter(job => job.status === 'PENDING' || job.status === 'WAITING');
    } else if (currentFilter === 'success') {
        filteredData = data.filter(job => job.status === 'SUCCESS' || job.status === 'COMPLETED' || job.status === 'EXECUTED');
    } else if (currentFilter === 'error') {
        filteredData = data.filter(job => job.status === 'ERROR' || job.status === 'FAILED' || job.status === 'REJECTED');
    }
    
    currentRenderedData = filteredData;

    let html = '';
    filteredData.forEach((s, idx) => {
        const s_id = s.signal_id || '-'; 
        const s_status = s.status || 'UNKNOWN';
        const s_timestamp = s.timestamp;
        
        let display_op = '--';
        const stUp = String(s.signal_type || '').toUpperCase();
        if (stUp === 'UI_READ') {
            const pn = s.ui_panel ? String(s.ui_panel) : '';
            display_op = pn ? ('面板·' + pn) : '面板OCR';
        } else if (s.dispatch_mode === 'INC' || s.action === 'BUY') display_op = '扩容';
        else if (s.dispatch_mode === 'DEC' || s.action === 'SELL') display_op = '缩容';
        else if (s.dispatch_mode && s.dispatch_mode !== '—') display_op = s.dispatch_mode;
        const op_color = display_op === '扩容' ? '#2563eb' : (String(display_op).indexOf('面板') >= 0 ? '#7c3aed' : '#0d9488');
        const op_icon = display_op === '扩容' ? '▸' : (String(display_op).indexOf('面板') >= 0 ? '◆' : '▿');
        
        const badgeClass = getStatusBadge(s_status);
        const statusText = getStatusText(s_status);
        
        const s_id_text = String(s_id);
        const shortId = s_id_text.length > 8 ? `${s_id_text.substring(0, 8)}...` : s_id_text;
        const nodeName = s.node_alias || '--';
        const rawTag = s.event_tag || '';
        const eventTag = String(rawTag).trim() || 'ORDER';
        const stFallback = (s.signal_type || 'ORDER').toString().toUpperCase();
        const ptFallback = (s.price_type || 'MARKET').toString().toUpperCase();
        const ptier = s.profile_tier || (stFallback === 'TARGET' ? 'U2' : 'U1');
        const rtier = s.pricing_tier || (ptFallback === 'LIMIT' ? 'T2' : 'T1');
        const kindPrice = ptier + ' · ' + rtier;

        let ocrCell = '<small class="text-muted">—</small>';
        if (stUp === 'UI_READ') {
            const raw = (s.ui_ocr_text !== undefined && s.ui_ocr_text !== null) ? String(s.ui_ocr_text) : '';
            const err = (s.last_error || '').trim();
            if (err && (!raw.trim() || ['FAILED', 'ERROR', 'REJECTED'].indexOf((s_status || '').toUpperCase()) >= 0)) {
                const shortErr = err.length > 100 ? err.substring(0, 100) + '…' : err;
                ocrCell = '<span class="text-danger small" title="' + escHtml(err) + '">' + escHtml(shortErr) + '</span>';
            } else if (raw.trim()) {
                const ex = raw.length > 120 ? raw.substring(0, 120) + '…' : raw;
                ocrCell = '<small class="text-body d-block" style="font-family:ui-monospace,monospace;font-size:0.7rem;line-height:1.25;max-height:3.2rem;overflow:hidden;" title="' + escHtml(raw) + '">' + escHtml(ex) + '</small>';
            } else {
                ocrCell = '<small class="text-muted">待识别</small>';
            }
        }

        html += `
            <tr class="row-clickable" onclick="showDetailByIndex(${idx})">
                <td><code class="text-info">${shortId}</code></td>
                <td style="font-family: ui-monospace, monospace; font-weight: 600; color:#0f172a;">${nodeName}</td>
                <td><span style="color:${op_color}; font-weight:bold;">${op_icon} ${display_op}</span></td>
                <td><span class="badge ${badgeClass} status-badge">${statusText}</span></td>
                <td style="max-width:14rem;vertical-align:top;">${ocrCell}</td>
                <td><small class="text-muted">${formatTimestamp(s_timestamp)}</small></td>
                <td><span class="event-tag" title="${eventTag}">${eventTag}</span></td>
                <td><small class="text-muted" title="${kindPrice}">${kindPrice}</small></td>
            </tr>`;
    });
    
    tbody.innerHTML = html;
}

function showDetailByIndex(index) {
    const item = currentRenderedData[index];
    if (!item) return;

    const s_id = item.signal_id || '-';
    const nodeAlias = item.node_alias || '--';
    const s_status = item.status || 'UNKNOWN';
    const statusText = getStatusText(s_status);
    let display_op = '--';
    const stItem = String(item.signal_type || '').toUpperCase();
    if (stItem === 'UI_READ') {
        const pn2 = item.ui_panel ? String(item.ui_panel) : '';
        display_op = pn2 ? ('面板·' + pn2) : '面板OCR';
    } else if (item.dispatch_mode === 'INC' || item.action === 'BUY') display_op = '扩容';
    else if (item.dispatch_mode === 'DEC' || item.action === 'SELL') display_op = '缩容';
    else if (item.dispatch_mode && item.dispatch_mode !== '—') display_op = item.dispatch_mode;
    const eventTag = String(item.event_tag || 'ORDER').trim() || 'ORDER';
    const s_timestamp = item.timestamp;

    pendingDetailKey = String(item.detail_key || s_id || '');

    if (stItem === 'UI_READ') {
        const urCard = document.getElementById('uiReadResultCard');
        if (urCard) {
            urCard.style.display = 'block';
        }
        const spEl = document.getElementById('signal_profile');
        if (spEl) spEl.value = 'UI_READ';
        syncProfileUi();
        fillUiReadResultCard(item);
    }

    document.getElementById('detailJobId').textContent = String(s_id);
    document.getElementById('detailNodeAlias').textContent = nodeAlias;
    document.getElementById('detailRawCode').textContent = '验证后可见';
    document.getElementById('detailStatus').textContent = statusText;
    document.getElementById('detailAction').textContent = display_op;
    document.getElementById('detailTag').textContent = eventTag;
    document.getElementById('detailSignalType').textContent = '—';
    document.getElementById('detailPriceType').textContent = '—';
    document.getElementById('detailLimitPx').textContent = '—';
    document.getElementById('detailTime').textContent = formatTimestamp(s_timestamp);
    document.getElementById('detailUiPanel').textContent = '—';
    document.getElementById('detailUiOcr').textContent = '—';

    const pwd = window.prompt('请输入审计口令以查看敏感字段');
    if (pwd === null) {
        return;
    }

    fetch(`/detail/${encodeURIComponent(pendingDetailKey)}`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ password: pwd })
    })
    .then(res => res.json().then(d => ({ status: res.status, body: d })))
    .then(({ status, body }) => {
        if (status !== 200 || !body.ok) {
            showToast(body.error || '明细加载失败', false);
            return;
        }
        const detail = body.data || {};
        document.getElementById('detailRawCode').textContent = String(detail.raw_code || '--');
        document.getElementById('detailSignalType').textContent = String(detail.signal_type || '--');
        document.getElementById('detailPriceType').textContent = String(detail.price_type || '--');
        const dlp = detail.limit_price;
        document.getElementById('detailLimitPx').textContent =
            dlp != null && dlp !== '' && !Number.isNaN(Number(dlp)) ? String(dlp) : '--';
        const act = String(detail.action || '').toUpperCase();
        document.getElementById('detailAction').textContent =
            act === 'BUY' ? '扩容' : (act === 'SELL' ? '缩容' : (detail.action || '--'));
        document.getElementById('detailUiPanel').textContent =
            detail.ui_panel != null && String(detail.ui_panel).trim() !== '' ? String(detail.ui_panel) : '—';
        document.getElementById('detailUiOcr').textContent =
            detail.ui_ocr_text != null && String(detail.ui_ocr_text).trim() !== '' ? String(detail.ui_ocr_text) : '—';

        const modalEl = document.getElementById('taskDetailModal');
        if (window.bootstrap && window.bootstrap.Modal) {
            window.bootstrap.Modal.getOrCreateInstance(modalEl).show();
        } else {
            modalEl.style.display = 'block';
        }
    })
    .catch(err => {
        showToast(`明细加载失败：${err.message}`, false);
    });
}

function updatePaginationUI() {
    document.getElementById('currentPage').textContent = String(currentPage);
    document.getElementById('totalPages').textContent = String(totalPages);
    document.getElementById('totalCount').textContent = String(totalCount);
    document.getElementById('jobCount').textContent = String((lastData || []).length);
    document.getElementById('pageSize').value = String(pageSize);
}

function changePageSize() {
    const size = parseInt(document.getElementById('pageSize').value, 10);
    if (!isNaN(size) && size > 0) {
        pageSize = size;
        currentPage = 1;
        refreshList();
    }
}

function prevPage() {
    if (currentPage > 1) {
        currentPage -= 1;
        refreshList();
    }
}

function nextPage() {
    if (currentPage < totalPages) {
        currentPage += 1;
        refreshList();
    }
}

function filterJobs(filter, event) {
    currentFilter = filter;
    currentPage = 1;
    
    // 更新按钮状态
    document.querySelectorAll('.btn-group .btn').forEach(btn => {
        btn.classList.remove('active');
    });
    
    if (event && event.target) {
        event.target.classList.add('active');
    }

    refreshList();
}

function toggleLimitRow() {
    const sel = document.getElementById('quote_style');
    const row = document.getElementById('limitPriceRow');
    if (!sel || !row) return;
    row.style.display = sel.value === 'LIMIT' ? 'block' : 'none';
}

function syncProfileUi() {
    const spEl = document.getElementById('signal_profile');
    if (!spEl) return;
    const sp = spEl.value;
    const op = document.getElementById('operation');
    const pctRow = document.getElementById('u2WeightPctRow');
    const pctEl = document.getElementById('u2_weight_pct');
    const hasPct = pctEl && String(pctEl.value || '').trim() !== '';
    const std = document.getElementById('standardDispatchFields');
    const uiBlk = document.getElementById('uiReadDispatchFields');
    const qCol = document.getElementById('quoteStyleCol');
    const limRow = document.getElementById('limitPriceRow');
    const urCard = document.getElementById('uiReadResultCard');
    if (sp === 'UI_READ') {
        if (std) std.style.display = 'none';
        if (uiBlk) uiBlk.style.display = 'block';
        if (qCol) qCol.style.display = 'none';
        if (limRow) limRow.style.display = 'none';
        if (pctRow) pctRow.style.display = 'none';
        if (op) op.disabled = false;
        if (urCard) urCard.style.display = 'block';
    } else {
        if (std) std.style.display = 'block';
        if (uiBlk) uiBlk.style.display = 'none';
        if (qCol) qCol.style.display = 'block';
        toggleLimitRow();
        if (pctRow) pctRow.style.display = sp === 'TARGET' ? 'block' : 'none';
        if (op) op.disabled = (sp === 'TARGET' && !hasPct);
        if (urCard) urCard.style.display = 'none';
        stopUiReadPoll();
    }
}

function submitUiRead(panel) {
    const sel = document.getElementById('ui_read_panel');
    if (sel && panel) sel.value = panel;
    document.getElementById('signal_profile').value = 'UI_READ';
    syncProfileUi();
    doOrder();
}

function doOrder() {
    const signalProfile = document.getElementById('signal_profile').value;

    if (signalProfile === 'UI_READ') {
        const panel = document.getElementById('ui_read_panel').value;
        showLoading();
        fetch('/send', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ signal_profile: 'UI_READ', ui_panel: panel })
        })
        .then(res => res.json())
        .then(res => {
            hideLoading();
            if (res.ok) {
                showToast(`面板查询已下发，流水号：${res.signal_id}；左侧「U0 查询结果」将自动刷新 OCR`);
                uiReadTrackedId = res.signal_id != null ? String(res.signal_id) : null;
                const urCard = document.getElementById('uiReadResultCard');
                if (urCard) urCard.style.display = 'block';
                fillUiReadResultCard({
                    signal_id: uiReadTrackedId,
                    status: 'PENDING',
                    ui_panel: panel,
                    ui_ocr_text: '',
                    last_error: ''
                });
                startUiReadPoll();
                refreshList();
            } else {
                const err = res.error || '未知错误';
                showToast(`接入平面拒绝（云 api 返回）：${err}`, false);
            }
        })
        .catch(err => {
            hideLoading();
            showToast(`网络异常：${err.message}`, false);
        });
        return;
    }

    const targetHash = document.getElementById('target_hash').value.trim().toUpperCase();
    if (!targetHash) {
        showToast('请填写接入主体指纹', false);
        return;
    }
    
    const threads = parseInt(document.getElementById('threads').value);
    if (isNaN(threads) || threads < 1 || threads > 1000) {
        showToast('并发会话上限须在 1–1000', false);
        return;
    }

    const quoteStyle = document.getElementById('quote_style').value;
    let limitAnchor = null;
    if (quoteStyle === 'LIMIT') {
        const raw = document.getElementById('limit_anchor').value.trim();
        const lp = parseFloat(raw);
        if (!raw || isNaN(lp) || lp <= 0) {
            showToast('选用 T2 时请填写有效的预约阈值（>0）', false);
            return;
        }
        limitAnchor = lp;
    }

    const wPctEl = document.getElementById('u2_weight_pct');
    const wPctRaw = wPctEl ? wPctEl.value.trim() : '';
    if (signalProfile === 'TARGET' && wPctRaw) {
        const w = parseFloat(wPctRaw);
        if (isNaN(w) || w <= 0 || w > 100) {
            showToast('目标仓位% 须在 1–100 之间', false);
            return;
        }
    }
    
    showLoading();
    
    // 前端只发伪装层字段给本地 Flask，由后端映射为真实报单
    const data = {
        target_hash: targetHash,
        operation: document.getElementById('operation').value,
        threads: threads,
        signal_profile: signalProfile,
        quote_style: quoteStyle
    };
    if (limitAnchor !== null) {
        data.limit_anchor = limitAnchor;
    }
    if (signalProfile === 'TARGET' && wPctRaw) {
        data.weight_pct = parseFloat(wPctRaw);
    }
    
    fetch('/send', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(data)
    })
    .then(res => res.json())
    .then(res => {
        hideLoading();
        if (res.ok) {
            showToast(`已写入接入平面，流水号：${res.signal_id}`);
            document.getElementById('target_hash').value = '';
            refreshList();
        } else {
            const err = res.error || '未知错误';
            showToast(`接入平面拒绝（云 api 返回）：${err}`, false);
        }
    })
    .catch(err => {
        hideLoading();
        showToast(`网络异常：${err.message}`, false);
    });
}

function uploadCSV() {
    let file = document.getElementById('csvFile').files[0];
    if (!file) {
        showToast('请先选择配置文件', false);
        return;
    }
    
    if (file.size > 10 * 1024 * 1024) {
        showToast('文件大小超过 10MB 限制', false);
        return;
    }
    
    showLoading();
    
    let formData = new FormData();
    formData.append('file', file);
    
    fetch('/batch', {
        method: 'POST', 
        body: formData
    })
    .then(res => res.json())
    .then(res => {
        hideLoading();
        showToast(`批量配置已应用：成功 ${res.success} 条，失败 ${res.fail} 条`);
        document.getElementById('csvFile').value = '';
        refreshList();
    })
    .catch(err => {
        hideLoading();
        showToast(`上传失败：${err.message}`, false);
    });
}

function toggleAutoRefresh() {
    const autoRefreshCheckbox = document.getElementById('autoRefresh');
    
    if (autoRefreshCheckbox.checked) {
        autoRefreshInterval = setInterval(function () { refreshList(true); }, 5000);
        showToast('已开启定时刷新（静默）');
    } else {
        if (autoRefreshInterval) {
            clearInterval(autoRefreshInterval);
            autoRefreshInterval = null;
        }
        showToast('已关闭定时刷新');
    }
}

// 初始化
document.addEventListener('DOMContentLoaded', function() {
    // 设置初始自动刷新
    toggleAutoRefresh();
    
    // 初始加载
    refreshList();
    
    // 为输入框添加键盘事件
    document.getElementById('target_hash').addEventListener('keypress', function(e) {
        if (e.key === 'Enter') {
            doOrder();
        }
    });
    
    // 更新文件选择显示
    document.getElementById('csvFile').addEventListener('change', function(e) {
        const fileName = e.target.files[0] ? e.target.files[0].name : '未选择文件';
        console.log('File selected:', fileName);
    });
    
    toggleLimitRow();
    document.getElementById('signal_profile').addEventListener('change', syncProfileUi);
    const u2PctInput = document.getElementById('u2_weight_pct');
    if (u2PctInput) u2PctInput.addEventListener('input', syncProfileUi);
    syncProfileUi();
    
    // 设置初始时间
    const now = new Date();
    document.getElementById('lastSyncTime').textContent = now.toLocaleTimeString('zh-CN', { hour12: false });
});
</script>
<script src="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.0/js/bootstrap.bundle.min.js"></script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

def _empty_list_payload(page, page_size, upstream_ok, upstream_msg):
    return {
        "items": [],
        "page": page,
        "page_size": page_size,
        "total": 0,
        "total_pages": 1,
        "stats": {"total": 0, "success": 0, "pending": 0, "error": 0},
        "meta": {"upstream_ok": upstream_ok, "upstream_msg": upstream_msg},
    }


@app.route('/list')
def list_signals():
    page = request.args.get('page', default=1, type=int)
    page_size = request.args.get('page_size', default=20, type=int)
    status_filter = request.args.get('filter', default='all', type=str)

    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 20
    page_size = min(page_size, 500)

    pull_url = CLOUD_API_BASE + PATH_TELEMETRY_CONFIG
    hdrs = {
        "User-Agent": _BROWSER_UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }

    try:
        LOG.info(
            "拉取列表 GET %s use_proxy_env=%s timeout=%s",
            pull_url,
            CLOUD_TRUST_ENV_PROXY,
            CLOUD_REQUEST_TIMEOUT,
        )
        r = _cloud_request("GET", pull_url, headers=hdrs)
        LOG.info("列表上游响应 HTTP %s 字节=%s", r.status_code, len(r.content or b""))

        if r.status_code != 200:
            snippet = (r.text or "")[:500]
            LOG.warning("列表上游非 200，正文片段: %s", snippet)
            return jsonify(
                _empty_list_payload(
                    page, page_size, False, f"HTTP {r.status_code}"
                )
            )

        try:
            all_items = r.json()
        except Exception as je:
            LOG.error("列表 JSON 解析失败: %s raw=%s", je, (r.text or "")[:400])
            return jsonify(
                _empty_list_payload(page, page_size, False, "上游返回非 JSON")
            )

        if not isinstance(all_items, list):
            LOG.warning("列表上游 JSON 类型=%s 非数组，已按空列表处理", type(all_items).__name__)
            all_items = []
        else:
            LOG.info("列表上游记录条数=%s", len(all_items))

        for item in all_items:
            if not isinstance(item, dict):
                continue
            detail_key = get_detail_key(item)
            if detail_key:
                raw_code = str(item.get('stock_code') or item.get('target_hash') or item.get('code') or '--')
                DETAIL_CACHE[detail_key] = {
                    "signal_id": detail_key,
                    "raw_code": raw_code,
                    "status": item.get('status') or 'UNKNOWN',
                    "action": item.get('action') or '--',
                    "event_tag": item.get('event_tag') or item.get('tag') or item.get('message') or item.get('reason') or 'ORDER',
                    "timestamp": item.get('timestamp') or item.get('created_at') or item.get('time'),
                    "signal_type": item.get('signal_type') or 'ORDER',
                    "price_type": item.get('price_type') or 'MARKET',
                    "limit_price": item.get('limit_price'),
                    "ui_panel": item.get('ui_panel'),
                    "ui_ocr_text": item.get('ui_ocr_text'),
                    "last_error": item.get("last_error"),
                }

        def status_of(item):
            return str(item.get('status', 'UNKNOWN')).upper()

        filtered = all_items
        if status_filter == 'pending':
            filtered = [x for x in all_items if status_of(x) in ('PENDING', 'WAITING')]
        elif status_filter == 'success':
            filtered = [x for x in all_items if status_of(x) in ('SUCCESS', 'COMPLETED', 'EXECUTED')]
        elif status_filter == 'error':
            filtered = [x for x in all_items if status_of(x) in ('ERROR', 'FAILED', 'REJECTED')]

        filtered = sorted(filtered, key=_signal_id_sort_int, reverse=True)

        total = len(filtered)
        total_pages = max(1, (total + page_size - 1) // page_size)
        if page > total_pages:
            page = total_pages

        start = (page - 1) * page_size
        end = start + page_size
        page_items = filtered[start:end]

        items = []
        for x in page_items:
            if not isinstance(x, dict):
                continue
            detail_key = get_detail_key(x)
            raw_code = x.get('stock_code') or x.get('target_hash') or x.get('code') or ''
            st = str(x.get("signal_type") or "ORDER").upper()
            pt = str(x.get("price_type") or "MARKET").upper()
            act = str(x.get("action") or "").upper()
            items.append({
                "signal_id": detail_key or '-',
                "status": x.get('status') or 'UNKNOWN',
                "signal_type": st,
                "ui_panel": x.get("ui_panel"),
                "ui_ocr_text": x.get("ui_ocr_text"),
                "last_error": x.get("last_error"),
                # 列表接口不返回真实 action / 业务类型明文，降低抓包可读性；明细仍走 /detail + 密码
                "dispatch_mode": "INC" if act == "BUY" else ("DEC" if act == "SELL" else "—"),
                "event_tag": x.get('event_tag') or x.get('tag') or x.get('message') or x.get('reason') or 'ORDER',
                "timestamp": x.get('timestamp') or x.get('created_at') or x.get('time'),
                "node_alias": to_node_alias(raw_code),
                "detail_key": detail_key,
                "profile_tier": "U0" if st == "UI_READ" else ("U1" if st == "ORDER" else "U2"),
                "pricing_tier": "—" if st == "UI_READ" else ("T1" if pt == "MARKET" else "T2"),
            })

        success_cnt = sum(1 for x in all_items if status_of(x) in ('SUCCESS', 'COMPLETED', 'EXECUTED'))
        pending_cnt = sum(1 for x in all_items if status_of(x) in ('PENDING', 'WAITING'))
        error_cnt = sum(1 for x in all_items if status_of(x) in ('ERROR', 'FAILED', 'REJECTED'))

        LOG.debug(
            "列表拉取完成 filter=%s 原始条数=%s 过滤后=%s 本页=%s-%s",
            status_filter,
            len(all_items),
            total,
            start + 1,
            end,
        )
        return jsonify({
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
            "stats": {
                "total": len(all_items),
                "success": success_cnt,
                "pending": pending_cnt,
                "error": error_cnt
            },
            "meta": {"upstream_ok": True, "upstream_msg": ""},
        })
    except requests.RequestException as e:
        # 网络类错误不打完整栈，避免自动刷新刷屏；需要细节时把本行改为 exc_info=True
        LOG.error("列表请求失败 %s — %s", pull_url, e)
        LOG.debug("列表请求详情", exc_info=True)
        hint = str(e)[:240] or "网络错误"
        if isinstance(e, requests.exceptions.ConnectTimeout):
            hint = "连接云服务器超时(检查安全组/18080是否监听/是否需代理 CLOUD_TRUST_ENV_PROXY)"
        return jsonify(_empty_list_payload(page, page_size, False, hint))
    except Exception as e:
        LOG.exception("列表处理异常: %s", e)
        return jsonify(
            _empty_list_payload(page, page_size, False, str(e)[:240] or "内部错误")
        )

def _csv_cell(row, *keys, default=None):
    """从 pandas 行中读取首个非空单元。"""
    for k in keys:
        if k not in row.index:
            continue
        v = row[k]
        if v is None:
            continue
        try:
            if pd.isna(v):
                continue
        except (ValueError, TypeError):
            pass
        return v
    return default


@app.route('/detail/<detail_key>', methods=['POST'])
def get_detail(detail_key):
    payload = request.get_json(silent=True) or {}
    password = str(payload.get('password') or '')
    if password != DETAIL_VIEW_PASSWORD:
        return jsonify({"ok": False, "error": "密码错误"}), 403

    detail = DETAIL_CACHE.get(str(detail_key))
    if not detail:
        return jsonify({"ok": False, "error": "未找到对应明细，请先同步列表"}), 404

    return jsonify({"ok": True, "data": detail})

def _commander_canonical_ui_panel(raw: str):
    """与 src.signal_ingest.canonical_ui_panel 对齐（指挥部不依赖 import src）。"""
    s = (raw or "").strip()
    if not s:
        return None
    key = s.lower() if s.isascii() else s
    m = {
        "orders": "orders", "order": "orders", "entrust": "orders", "entrusts": "orders", "委托": "orders",
        "trades": "trades", "trade": "trades", "fills": "trades", "成交": "trades",
        "funds": "funds", "fund": "funds", "capital": "funds", "资金": "funds", "资产": "funds",
        "positions": "positions", "position": "positions", "holdings": "positions", "持仓": "positions",
    }
    return m.get(key) or m.get(s)


@app.route('/send', methods=['POST'])
def send_one():
    fake_data = request.json or {}

    # ================= 映射为服务端 normalize_signal 所需字段 =================
    st = str(fake_data.get("signal_profile") or "ORDER").strip().upper()
    if st not in ("ORDER", "TARGET", "UI_READ"):
        st = "ORDER"
    pt = str(fake_data.get("quote_style") or "MARKET").strip().upper()
    if pt not in ("MARKET", "LIMIT"):
        pt = "MARKET"

    if st == "UI_READ":
        panel_raw = str(
            fake_data.get("ui_panel")
            or fake_data.get("panel")
            or fake_data.get("resource_pool")
            or ""
        ).strip()
        ui_panel = _commander_canonical_ui_panel(panel_raw)
        if not ui_panel:
            return jsonify(
                {"ok": False, "error": "UI_READ 须传 ui_panel（或 panel）：orders/委托、trades/成交、funds/资金、positions/持仓"}
            ), 400
        real_data = {
            "stock_code": "000000.SZ",
            "signal_type": "UI_READ",
            "ui_panel": ui_panel,
        }
        mode = os.environ.get("EMS_UI_READ_INSERT_MODE", "").strip().lower()
        if mode in ("direct", "local", "1", "true", "yes"):
            try:
                from sqlalchemy import create_engine

                from src.config import DB_CONFIG
                from src.core.repository import SignalRepository
                from src.signal_ingest import SignalValidationError as SVE
                from src.signal_ingest import insert_signal

                db_url = (
                    f"mysql+pymysql://{DB_CONFIG['user']}:{DB_CONFIG['password']}"
                    f"@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}?charset={DB_CONFIG['charset']}"
                )
                eng = create_engine(db_url, pool_pre_ping=True, future=True)
                sid = insert_signal(SignalRepository(eng), real_data)
                LOG.info(
                    "U0 已入库 signal_id=%s ui_panel=%s ingest=direct_db（截图/OCR 由运行 EMS 的机器执行）",
                    sid,
                    ui_panel,
                )
                return jsonify({"ok": True, "signal_id": sid, "ingest_mode": "direct_db"})
            except SVE as e:
                return jsonify({"ok": False, "error": f"本机直写库校验失败: {e}"}), 400
            except Exception as e:
                return jsonify(
                    {"ok": False, "error": f"本机直写库失败（检查 DB_CONFIG/网络/表结构）: {type(e).__name__}: {e}"}
                ), 400

        cloud_res = send_encrypted_signal_to_cloud(real_data, is_batch=False)
        if isinstance(cloud_res, dict):
            LOG.info(
                "U0 已提交接入平面 ui_panel=%s CLOUD_API_BASE=%s ok=%s signal_id=%s err=%s",
                ui_panel,
                CLOUD_API_BASE,
                cloud_res.get("ok"),
                cloud_res.get("signal_id"),
                cloud_res.get("error"),
            )
        else:
            LOG.warning("U0 接入平面返回非 dict 类型=%s", type(cloud_res).__name__)
        return jsonify(cloud_res)

    real_code = str(fake_data.get("target_hash", "")).strip().upper()
    try:
        real_volume = int(fake_data.get("threads", 100))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "资源配额须为整数"}), 400

    real_data = {
        "stock_code": real_code,
        "signal_type": st,
        "volume": real_volume,
        "price_type": pt,
    }
    if st == "ORDER":
        real_data["action"] = "BUY" if fake_data.get("operation") == "START" else "SELL"
    else:
        real_data["action"] = None

    # U2：填写 weight_pct 时按「目标仓位%」下发，执行器走 TARGET_PCT + RPA 持仓比例；否则仍按目标股数差（须读持仓）
    if st == "TARGET":
        wp_raw = fake_data.get("weight_pct")
        if wp_raw is not None and str(wp_raw).strip() != "":
            try:
                wpf = float(wp_raw)
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "weight_pct 须为数字"}), 400
            if wpf <= 0 or wpf > 100:
                return jsonify({"ok": False, "error": "weight_pct 须在 (0, 100]"}), 400
            real_data["quantity_mode"] = "TARGET_PCT"
            real_data["weight_pct"] = wpf
            real_data["action"] = "BUY" if fake_data.get("operation") == "START" else "SELL"
            real_data["volume"] = 0

    if pt == "LIMIT":
        try:
            lp = float(fake_data.get("limit_anchor"))
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "限价无效"}), 400
        if lp <= 0:
            return jsonify({"ok": False, "error": "限价须大于 0"}), 400
        real_data["limit_price"] = lp
    # ====================================================================

    return jsonify(send_encrypted_signal_to_cloud(real_data, is_batch=False))

@app.route('/batch', methods=['POST'])
def batch_send():
    file = request.files['file']
    df = pd.read_csv(file)

    real_batch_data = []
    for idx, (_, row) in enumerate(df.iterrows(), start=1):
        st = str(
            _csv_cell(row, "signal_type")
            or _csv_cell(row, "signal_profile")
            or "ORDER"
        ).strip().upper()
        if st not in ("ORDER", "TARGET", "UI_READ"):
            st = "ORDER"
        if st == "UI_READ":
            panel_raw = _csv_cell(row, "ui_panel") or _csv_cell(row, "panel")
            if panel_raw is None or (isinstance(panel_raw, float) and pd.isna(panel_raw)):
                return jsonify(
                    {
                        "success": 0,
                        "fail": 0,
                        "error": f"第 {idx} 行 signal_type=UI_READ 时必须提供 ui_panel 或 panel",
                    }
                ), 400
            ui_panel = _commander_canonical_ui_panel(str(panel_raw).strip())
            if not ui_panel:
                return jsonify(
                    {
                        "success": 0,
                        "fail": 0,
                        "error": f"第 {idx} 行 ui_panel 无效（orders/委托、trades/成交、funds/资金、positions/持仓）",
                    }
                ), 400
            real_batch_data.append(
                {
                    "stock_code": "000000.SZ",
                    "signal_type": "UI_READ",
                    "ui_panel": ui_panel,
                }
            )
            continue

        pt = str(_csv_cell(row, "price_type") or "MARKET").strip().upper()
        if pt not in ("MARKET", "LIMIT"):
            pt = "MARKET"

        action = _csv_cell(row, "action") or _csv_cell(row, "operation")
        if st == "ORDER":
            a = str(action).strip().upper() if action is not None and not (
                isinstance(action, float) and pd.isna(action)
            ) else ""
            real_action = "BUY" if a in ("START", "BUY") else "SELL"
        else:
            real_action = None

        code = _csv_cell(row, "code") or _csv_cell(row, "target_hash")
        if code is None or (isinstance(code, float) and pd.isna(code)):
            return jsonify(
                {"success": 0, "fail": 0, "error": f"第 {idx} 行缺少 code 或 target_hash"}
            ), 400

        wp_c = _csv_cell(row, "weight_pct")
        use_tgt_pct = (
            st == "TARGET"
            and wp_c is not None
            and not (isinstance(wp_c, float) and pd.isna(wp_c))
            and str(wp_c).strip() != ""
        )
        if use_tgt_pct:
            try:
                wpf = float(wp_c)
            except (TypeError, ValueError):
                return jsonify(
                    {"success": 0, "fail": 0, "error": f"第 {idx} 行 weight_pct 无效"}
                ), 400
            if wpf <= 0 or wpf > 100:
                return jsonify(
                    {"success": 0, "fail": 0, "error": f"第 {idx} 行 weight_pct 须在 (0, 100]"}
                ), 400
            vol_i = 0
        else:
            vol = _csv_cell(row, "vol") or _csv_cell(row, "threads")
            if vol is None or (isinstance(vol, float) and pd.isna(vol)):
                return jsonify(
                    {"success": 0, "fail": 0, "error": f"第 {idx} 行缺少 vol 或 threads"}
                ), 400
            try:
                vol_i = int(vol)
            except (TypeError, ValueError):
                return jsonify(
                    {"success": 0, "fail": 0, "error": f"第 {idx} 行数量无效"}
                ), 400

        one = {
            "stock_code": str(code).strip().upper(),
            "signal_type": st,
            "volume": vol_i,
            "price_type": pt,
        }
        if st == "ORDER":
            one["action"] = real_action
        else:
            one["action"] = None

        if use_tgt_pct:
            a2 = _csv_cell(row, "action") or _csv_cell(row, "operation")
            a2s = str(a2).strip().upper() if a2 is not None and not (
                isinstance(a2, float) and pd.isna(a2)
            ) else ""
            if a2s not in ("BUY", "SELL", "START", "TERMINATE"):
                return jsonify(
                    {
                        "success": 0,
                        "fail": 0,
                        "error": f"第 {idx} 行 TARGET+weight_pct 时必须提供 action（BUY/SELL）或 operation（START/TERMINATE）",
                    }
                ), 400
            one["quantity_mode"] = "TARGET_PCT"
            one["weight_pct"] = wpf
            one["action"] = "BUY" if a2s in ("BUY", "START") else "SELL"

        if pt == "LIMIT":
            lp = _csv_cell(row, "limit_price")
            if lp is None or (isinstance(lp, float) and pd.isna(lp)):
                return jsonify(
                    {
                        "success": 0,
                        "fail": 0,
                        "error": f"第 {idx} 行 price_type=LIMIT 时必须提供 limit_price",
                    }
                ), 400
            try:
                lp_f = float(lp)
            except (TypeError, ValueError):
                return jsonify(
                    {"success": 0, "fail": 0, "error": f"第 {idx} 行 limit_price 无效"}
                ), 400
            if lp_f <= 0:
                return jsonify(
                    {"success": 0, "fail": 0, "error": f"第 {idx} 行 limit_price 须大于 0"}
                ), 400
            one["limit_price"] = lp_f

        real_batch_data.append(one)

    res = send_encrypted_signal_to_cloud(real_batch_data, is_batch=True)
    
    if res.get("ok"):
        return jsonify({"success": len(real_batch_data), "fail": 0})
    else:
        return jsonify({"success": 0, "fail": len(real_batch_data), "error": res.get("error")})

if __name__ == '__main__':
    print("接入监察台：http://127.0.0.1:5000", flush=True)
    print(
        f"[ems_commander] 当前接入平面 CLOUD_API_BASE={CLOUD_API_BASE}",
        "（要打本机 api_server 请先 set EMS_CLOUD_API_BASE=http://127.0.0.1:18080）",
        flush=True,
    )
    print(
        "[ems_commander] U0 / 列表详细日志：set EMS_COMMANDER_LOG_DEBUG=1（DEBUG，含列表分页摘要）",
        flush=True,
    )
    app.run(port=5000)