import json
import pandas as pd
from flask import Flask, render_template_string, request, jsonify
import requests
import time
import hmac
import hashlib
from cryptography.fernet import Fernet

# ================= 配置区 =================
CLOUD_API_URL = "http://120.53.250.208:18080/signals"
API_KEY = "strategy01"
API_SECRET = "Qwer!1234567"

# 必须和云端完全一致的密钥！
ENCRYPTION_KEY = b'9T71nh6mIWjZIm96LKuYK3-u3AEv5RhiqPWEorKJRcQ='
CIPHER_SUITE = Fernet(ENCRYPTION_KEY)
DETAIL_VIEW_PASSWORD = "123456"
# =========================================

app = Flask(__name__)

DETAIL_CACHE = {}

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
    
    # 3. 构造要发往公网的安全 Payload (不含有任何交易信息)
    safe_payload = {"encrypted_payload": encrypted_text}
    
    # 4. 对安全 Payload 计算签名
    body_text = json.dumps(safe_payload, sort_keys=True, separators=(',', ':'))
    body_hash = hashlib.sha256(body_text.encode()).hexdigest()
    
    method = "POST"
    path = "/signals/batch" if is_batch else "/signals"
    sign_message = f"{method}\n{path}\n{timestamp}\n{nonce}\n{body_hash}"
    
    signature = hmac.new(
        API_SECRET.encode(), 
        sign_message.encode(), 
        hashlib.sha256
    ).hexdigest()
    
    headers = {
        "X-API-KEY": API_KEY,
        "X-SIGNATURE": signature,
        "X-TIMESTAMP": timestamp,
        "X-NONCE": nonce,
        "Content-Type": "application/json"
    }
    
    try:
        url = CLOUD_API_URL + "/batch" if is_batch else CLOUD_API_URL
        r = requests.post(url, json=safe_payload, headers=headers, timeout=5)
        print(safe_payload)  # 调试：查看发送的安全载荷
        return r.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}
    

# ================= 伪装版前端 HTML =================
# 伪装成 DevOps 服务器管理面板
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>分布式任务编排控制台 v3.0</title>
    <link href="https://cdn.bootcdn.net/ajax/libs/twitter-bootstrap/5.3.0/css/bootstrap.min.css" rel="stylesheet">
    <link href="https://cdn.bootcdn.net/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <style>
        :root {
            --bg: #0b1220;
            --bg-soft: #111a2e;
            --card: rgba(15, 23, 42, 0.78);
            --card-border: #23314f;
            --text: #d8e4ff;
            --muted: #8aa0c8;
            --primary: #4f8cff;
            --primary-2: #2f6fe6;
            --success: #1fbf75;
            --warning: #f3b34c;
            --danger: #f25f6b;
            --accent: #8b7bff;
        }
        body {
            padding: 18px;
            background:
                radial-gradient(circle at 20% 10%, rgba(79,140,255,0.25), transparent 35%),
                radial-gradient(circle at 80% 0%, rgba(139,123,255,0.18), transparent 32%),
                linear-gradient(160deg, var(--bg) 0%, #0f1730 55%, #16153a 100%);
            color: var(--text);
            min-height: 100vh;
            font-family: 'Microsoft YaHei UI', 'Segoe UI', system-ui, sans-serif;
        }
        .card {
            background: var(--card);
            border: 1px solid var(--card-border);
            margin-bottom: 16px;
            border-radius: 14px;
            backdrop-filter: blur(8px);
            box-shadow: 0 8px 30px rgba(0, 0, 0, 0.24);
            transition: all .22s ease;
        }
        .card:hover {
            transform: translateY(-2px);
            border-color: #30466e;
        }
        .card-header {
            background: rgba(8, 15, 31, .72);
            border-bottom: 1px solid var(--card-border);
            border-radius: 14px 14px 0 0 !important;
            padding: .92rem 1.15rem;
            font-weight: 600;
            letter-spacing: .2px;
        }
        .table { color: var(--text); }
        .table-dark {
            --bs-table-bg: #1a2642;
            --bs-table-border-color: #324a75;
            color: #dce8ff;
        }
        .table-hover tbody tr:hover {
            background-color: rgba(79,140,255,0.12);
            color: #fff;
        }
        .table > :not(caption) > * > * {
            background-color: rgba(16, 26, 47, .58);
            color: #d7e5ff;
            border-bottom-color: #2a3d63;
        }
        .table tbody tr:nth-child(even) td {
            background-color: rgba(21, 33, 58, .72);
        }
        .row-clickable {
            cursor: pointer;
        }
        .detail-code {
            font-family: 'Courier New', monospace;
            color: #d9e7ff;
            background: rgba(40, 62, 102, .4);
            border: 1px solid #3a5787;
            border-radius: 8px;
            padding: .35rem .5rem;
            display: inline-block;
        }
        .form-control, .form-select {
            background: #1d2b47;
            border: 1px solid #344a73;
            color: #f0f6ff;
            border-radius: 9px;
            padding: .68rem .92rem;
        }
        .form-control::placeholder { color: #8ca4d1; }
        .input-group-text {
            background: #1d2b47;
            border: 1px solid #344a73;
            color: #cfe0ff;
        }
        .text-muted {
            color: #c3d4f2 !important;
            opacity: .95;
        }
        .form-control:focus, .form-select:focus {
            background: #1d2b47;
            color: #fff;
            border-color: var(--primary);
            box-shadow: 0 0 0 3px rgba(79,140,255,.24);
        }
        .btn {
            border-radius: 9px;
            padding: .64rem 1.1rem;
            font-weight: 600;
            border: none;
        }
        .btn-primary { background: linear-gradient(90deg, var(--primary), var(--primary-2)); }
        .btn-success { background: linear-gradient(90deg, #22c783, #169e65); }
        .btn-outline-secondary {
            border: 1px solid #4b628e;
            color: #c1d2f4;
            background: rgba(29,43,71,.35);
        }
        .btn-outline-secondary:hover { background: rgba(79,140,255,.2); color: #fff; }
        .metric-card { border-left: 4px solid; padding: .9rem; margin-bottom: .85rem; min-height: 102px; }
        .metric-value {
            font-size: 1.9rem;
            font-weight: 700;
            line-height: 1.05;
            color: #eef4ff;
            text-shadow: 0 1px 8px rgba(79,140,255,.18);
        }
        .metric-label { color: #d7e5ff; font-size: .82rem; letter-spacing: .4px; font-weight: 600; }
        .metric-sub { color: #b9ccef; font-size: .76rem; }
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
            background: rgba(6, 12, 25, .82);
            display: none;
            align-items: center;
            justify-content: center;
            z-index: 9999;
        }
        .spinner {
            width: 52px;
            height: 52px;
            border: 3px solid rgba(79,140,255,.25);
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
            background: rgba(10, 19, 37, .85);
            border: 1px solid var(--card-border);
            border-radius: 14px;
            padding: .9rem 1rem;
            margin-bottom: 1rem;
        }
        .cluster-status { display: flex; align-items: center; gap: 9px; font-size: .88rem; color: #aac0e6; }
        .cluster-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background: var(--success);
            box-shadow: 0 0 0 0 rgba(31,191,117,.6);
            animation: pulse 2s infinite;
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
            background: rgba(23, 35, 60, .5);
            border: 1px solid #2f466f;
            border-radius: 10px;
            padding: .6rem .7rem;
        }
        .info-item .k { color: #95aad2; font-size: .76rem; }
        .info-item .v { color: #e7efff; font-size: .88rem; font-weight: 600; }
        .activity-log {
            background: rgba(20, 32, 58, .72);
            border: 1px solid #35507f;
            border-radius: 12px;
            padding: .9rem;
        }
        .activity-log .entry {
            border-left: 2px solid #4f73b0;
            padding-left: .8rem;
            margin-bottom: .65rem;
        }
        .activity-log small {
            color: #dce9ff !important;
            font-weight: 500;
        }
        .badge-soft {
            display: inline-block;
            background: rgba(79,140,255,.15);
            color: #c8daff;
            border: 1px solid rgba(79,140,255,.38);
            border-radius: 999px;
            font-size: .72rem;
            padding: .18rem .54rem;
        }
        .table-footer-text {
            color: #d6e4ff;
            font-weight: 600;
        }
        .event-tag {
            display: inline-block;
            padding: .18rem .56rem;
            border-radius: 999px;
            font-size: .72rem;
            color: #edf4ff;
            background: rgba(96, 126, 196, .46);
            border: 1px solid rgba(146, 178, 242, .72);
            max-width: 150px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            vertical-align: middle;
        }
        .form-check-label {
            color: #e8f0ff;
            font-weight: 600;
        }
        .card-header, .card-header span, .card-header i {
            color: #e7f0ff;
        }
        .card-body label {
            color: #e1ecff;
            font-weight: 600;
        }
        .activity-log .status-badge {
            color: #0d1a33;
            font-weight: 700;
        }
    </style>
</head>
<body>
    <div class="loading-overlay" id="loadingOverlay">
        <div class="spinner"></div>
        <div class="ms-3" style="color: #e2e8f0; font-weight: 600;">正在编排并同步任务数据，请稍候...</div>
    </div>

    <div class="toast" id="successToast" style="display: none;">
        <div class="card bg-success text-white border-0">
            <div class="card-body p-3">
                <div class="d-flex align-items-center">
                    <i class="fas fa-check-circle fa-lg me-3"></i>
                    <div>
                        <h6 class="mb-0">操作成功</h6>
                        <small id="toastMessage">任务已成功执行</small>
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
                        <h6 class="mb-0">操作失败</h6>
                        <small id="errorToastMessage">执行过程中出现错误</small>
                    </div>
                    <button type="button" class="btn-close btn-close-white ms-auto" onclick="hideToast('errorToast')"></button>
                </div>
            </div>
        </div>
    </div>

    <nav class="navbar navbar-expand-lg">
        <div class="container">
            <a class="navbar-brand d-flex align-items-center" href="#">
                <i class="fas fa-server fa-xl me-2" style="color: #3b82f6;"></i>
                <div>
                    <h4 class="mb-0" style="color:#e2e8f0;">分布式任务编排控制台</h4>
                    <div class="cluster-status">
                        <div class="cluster-dot"></div>
                        <span>系统状态：<strong>正常</strong> ｜ 在线节点：24 ｜ 控制平面：v3.0</span>
                    </div>
                </div>
            </a>
            <div class="d-flex align-items-center">
                <div class="me-3">
                    <small class="text-muted">最近同步：<span id="lastSyncTime">--:--:--</span></small>
                </div>
                <button class="btn btn-outline-secondary" onclick="refreshList()">
                    <i class="fas fa-sync-alt me-2"></i>立即同步
                </button>
            </div>
        </div>
    </nav>

    <div class="modal fade" id="taskDetailModal" tabindex="-1" aria-hidden="true">
      <div class="modal-dialog modal-lg modal-dialog-centered">
        <div class="modal-content" style="background:#131f37; border:1px solid #2d446f; color:#dce8ff; border-radius:14px;">
          <div class="modal-header" style="border-bottom:1px solid #2d446f;">
            <h5 class="modal-title"><i class="fas fa-circle-info me-2"></i>任务明细</h5>
            <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal" aria-label="Close"></button>
          </div>
          <div class="modal-body">
            <div class="row g-3">
              <div class="col-md-6"><small class="text-muted d-block">任务ID</small><div id="detailJobId">-</div></div>
              <div class="col-md-6"><small class="text-muted d-block">节点别名</small><div id="detailNodeAlias">-</div></div>
              <div class="col-md-6"><small class="text-muted d-block">脱敏代码（密码验证后加载）</small><div id="detailRawCode" class="detail-code">未加载</div></div>
              <div class="col-md-6"><small class="text-muted d-block">执行状态</small><div id="detailStatus">-</div></div>
              <div class="col-md-6"><small class="text-muted d-block">调度动作</small><div id="detailAction">-</div></div>
              <div class="col-md-6"><small class="text-muted d-block">事件标签</small><div id="detailTag">-</div></div>
              <div class="col-12"><small class="text-muted d-block">时间戳</small><div id="detailTime">-</div></div>
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
                <div class="metric-label">活动任务数</div>
                <div class="metric-sub">当前待处理 + 已完成 + 异常</div>
            </div>
        </div>
        <div class="col-md-3">
            <div class="card metric-card" style="border-left-color: #10b981;">
                <div class="metric-value" id="successRate">100%</div>
                <div class="metric-label">任务成功率</div>
                <div class="metric-sub">成功 / 总任务（实时）</div>
            </div>
        </div>
        <div class="col-md-3">
            <div class="card metric-card" style="border-left-color: #f59e0b;">
                <div class="metric-value" id="avgLatency">0ms</div>
                <div class="metric-label">平均响应延迟</div>
                <div class="metric-sub">控制节点到上游接口估算</div>
            </div>
        </div>
        <div class="col-md-3">
            <div class="card metric-card" style="border-left-color: #8b5cf6;">
                <div class="metric-value" id="cpuUtil">--%</div>
                <div class="metric-label">资源占用率</div>
                <div class="metric-sub">控制节点当前估算值</div>
            </div>
        </div>
    </div>
    
    <div class="row">
        <div class="col-lg-5">
            <div class="card shadow-sm">
                <div class="card-header d-flex align-items-center justify-content-between">
                    <span><i class="fas fa-cogs me-2"></i>任务编排控制</span>
                    <span class="badge bg-info">手动调度</span>
                </div>
                <div class="card-body">
                    <div class="mb-4">
                        <h5 class="mb-3" style="color:#e2e8f0;"><i class="fas fa-terminal me-2"></i>节点调度参数</h5>
                        <div class="mb-3">
                            <label class="form-label mb-2">目标节点标识（Hash）</label>
                            <input type="text" id="target_hash" class="form-control" placeholder="请输入节点标识，例如 NODE-A1 / REGION-X7" style="font-family: 'Courier New', monospace;">
                            <small class="text-muted">用于定位任务目标节点，不涉及业务字段</small>
                        </div>
                        
                        <div class="row g-3 mb-3">
                            <div class="col-md-6">
                                <label class="form-label mb-2">调度动作</label>
                                <select id="operation" class="form-select">
                                    <option value="START">🚀 启动编排任务（扩容）</option>
                                    <option value="TERMINATE">🛑 终止编排任务（缩容）</option>
                                </select>
                            </div>
                            <div class="col-md-6">
                                <label class="form-label mb-2">资源配额</label>
                                <div class="input-group">
                                    <input type="number" id="threads" class="form-control" value="100" min="1" max="1000">
                                    <span class="input-group-text">线程</span>
                                </div>
                                <small class="text-muted">控制节点线程配额范围（1-1000）</small>
                            </div>
                        </div>
                        
                        <button class="btn btn-primary w-100 py-2" onclick="doOrder()">
                            <i class="fas fa-rocket me-2"></i>执行调度
                        </button>
                    </div>
                    
                    <hr style="border-color:#475569;">
                    
                    <div>
                        <h5 class="mb-3" style="color:#e2e8f0;"><i class="fas fa-file-import me-2"></i>批量任务导入</h5>
                        <div class="mb-3">
                            <label class="form-label mb-2">上传批量配置文件</label>
                            <div class="input-group">
                                <input type="file" id="csvFile" class="form-control" accept=".csv">
                                <button class="btn btn-outline-secondary" type="button" onclick="document.getElementById('csvFile').click()">
                                    <i class="fas fa-folder-open"></i>
                                </button>
                            </div>
                            <small class="text-muted">仅支持 CSV，最大 10MB。字段可为 action/code/vol 或 operation/target_hash/threads。</small>
                        </div>
                        <button class="btn btn-success w-100 py-2" onclick="uploadCSV()">
                            <i class="fas fa-cloud-upload-alt me-2"></i>应用批量配置
                        </button>
                    </div>
                </div>
            </div>
            
            <div class="card shadow-sm mt-4">
                <div class="card-header">
                    <i class="fas fa-chart-line me-2"></i>系统健康指标
                </div>
                <div class="card-body">
                    <div class="d-flex justify-content-between align-items-center mb-3">
                        <small>集群负载</small>
                        <small class="text-success"><i class="fas fa-arrow-up me-1"></i><span id="clusterLoadText">65%</span></small>
                    </div>
                    <div class="progress mb-4" style="height: 8px; background: #334155;">
                        <div class="progress-bar bg-success" id="clusterLoadBar" style="width: 65%"></div>
                    </div>
                    
                    <div class="d-flex justify-content-between align-items-center mb-3">
                        <small>网络吞吐</small>
                        <small id="networkThroughputText">1.2 Gb/s</small>
                    </div>
                    <div class="progress mb-4" style="height: 8px; background: #334155;">
                        <div class="progress-bar bg-info" id="networkThroughputBar" style="width: 80%"></div>
                    </div>
                    
                    <div class="d-flex justify-content-between align-items-center mb-3">
                        <small>存储 IOPS</small>
                        <small id="storageIopsText">45k</small>
                    </div>
                    <div class="progress" style="height: 8px; background: #334155;">
                        <div class="progress-bar bg-warning" id="storageIopsBar" style="width: 45%"></div>
                    </div>
                </div>
            </div>

            <div class="card shadow-sm mt-4">
                <div class="card-header d-flex justify-content-between align-items-center">
                    <span><i class="fas fa-circle-info me-2"></i>运行上下文</span>
                    <span class="badge-soft">透明展示</span>
                </div>
                <div class="card-body">
                    <div class="info-grid">
                        <div class="info-item"><div class="k">接口基址</div><div class="v">/signals</div></div>
                        <div class="info-item"><div class="k">通信模式</div><div class="v">HTTPS/API + 签名</div></div>
                        <div class="info-item"><div class="k">数据形态</div><div class="v">加密载荷 + 安全头</div></div>
                        <div class="info-item"><div class="k">同步策略</div><div class="v">手动 + 自动轮询</div></div>
                        <div class="info-item"><div class="k">批量方式</div><div class="v">CSV 导入</div></div>
                        <div class="info-item"><div class="k">追踪粒度</div><div class="v">任务级别</div></div>
                    </div>
                </div>
            </div>
        </div>

        <div class="col-lg-7">
            <div class="card shadow-sm">
                <div class="card-header d-flex align-items-center justify-content-between">
                    <span><i class="fas fa-tasks me-2"></i>编排任务明细</span>
                    <div class="d-flex align-items-center">
                        <div class="form-check form-switch me-3">
                            <input class="form-check-input" type="checkbox" id="autoRefresh" checked onchange="toggleAutoRefresh()">
                            <label class="form-check-label" for="autoRefresh">自动刷新</label>
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
                                    <th style="width: 15%;">任务ID</th>
                                    <th style="width: 22%;">节点标识</th>
                                    <th style="width: 16%;">调度动作</th>
                                    <th style="width: 16%;">执行状态</th>
                                    <th style="width: 17%;">时间戳</th>
                                    <th style="width: 14%;">事件标签</th>
                                </tr>
                            </thead>
                            <tbody id="signalList">
                                <tr>
                                    <td colspan="6" class="text-center py-5">
                                        <div class="text-muted">
                                            <i class="fas fa-inbox fa-2x mb-3"></i>
                                            <p>暂无任务记录，可先发起一次调度。</p>
                                        </div>
                                    </td>
                                </tr>
                            </tbody>
                        </table>
                    </div>
                </div>
                <div class="card-footer">
                    <div class="d-flex justify-content-between align-items-center flex-wrap gap-2">
                        <small class="table-footer-text">第 <span id="currentPage">1</span>/<span id="totalPages">1</span> 页，共 <span id="totalCount">0</span> 条，当前页 <span id="jobCount">0</span> 条</small>
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
                    <i class="fas fa-history me-2"></i>最近活动日志
                </div>
                <div class="card-body">
                    <div class="activity-log" style="max-height: 200px; overflow-y: auto;">
                        <div class="d-flex align-items-start mb-3">
                            <div class="status-badge bg-success me-3">INFO</div>
                            <div class="entry">
                                <small class="d-block text-muted">刚刚</small>
                                <small>控制台初始化完成，系统服务均已就绪。</small>
                            </div>
                        </div>
                        <div class="d-flex align-items-start mb-3">
                            <div class="status-badge bg-info me-3">DEBUG</div>
                            <div class="entry">
                                <small class="d-block text-muted">2 分钟前</small>
                                <small>健康检查完成，24/24 节点状态可达。</small>
                            </div>
                        </div>
                        <div class="d-flex align-items-start">
                            <div class="status-badge bg-warning me-3">WARN</div>
                            <div class="entry">
                                <small class="d-block text-muted">15 分钟前</small>
                                <small>节点 N7 出现短时延迟抖动，已自动恢复。</small>
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

function refreshList() {
    showLoading();

    const params = new URLSearchParams({
        page: String(currentPage),
        page_size: String(pageSize),
        filter: currentFilter
    });
    
    fetch(`/list?${params.toString()}`)
        .then(res => res.json())
        .then(payload => {
            const data = payload.items || [];
            lastData = data;
            currentPage = payload.page || 1;
            pageSize = payload.page_size || pageSize;
            totalPages = payload.total_pages || 1;
            totalCount = payload.total || data.length;

            updateMetrics(data, payload.stats || null);
            renderTable(data);
            updatePaginationUI();
            hideLoading();
        })
        .catch(err => {
            console.error("Sync failed:", err);
            showToast('同步失败：无法连接上游节点', false);
            hideLoading();
        });
}

function renderTable(data) {
    const tbody = document.getElementById('signalList');
    
    if (!data || data.length === 0) {
        currentRenderedData = [];
        tbody.innerHTML = `
            <tr>
                <td colspan="6" class="text-center py-5">
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
        
        // 逆向伪装：如果是真正的云端查回来的 BUY，显示为 START
        const display_op = s.action === 'BUY' ? 'START' : (s.action === 'SELL' ? 'TERMINATE' : s.action);
        const op_color = display_op === 'START' ? '#ef4444' : '#10b981';
        const op_icon = display_op === 'START' ? '🚀' : '🛑';
        
        const badgeClass = getStatusBadge(s_status);
        const statusText = getStatusText(s_status);
        
        const s_id_text = String(s_id);
        const shortId = s_id_text.length > 8 ? `${s_id_text.substring(0, 8)}...` : s_id_text;
        const nodeName = s.node_alias || '--';
        const rawTag = s.event_tag || '';
        const eventTag = String(rawTag).trim() || 'ORDER';

        html += `
            <tr class="row-clickable" onclick="showDetailByIndex(${idx})">
                <td><code class="text-info">${shortId}</code></td>
                <td style="font-family: 'Courier New', monospace; font-weight: 600; color:#dfeafe;">${nodeName}</td>
                <td><span style="color:${op_color}; font-weight:bold;">${op_icon} ${display_op}</span></td>
                <td><span class="badge ${badgeClass} status-badge">${statusText}</span></td>
                <td><small class="text-muted">${formatTimestamp(s_timestamp)}</small></td>
                <td><span class="event-tag" title="${eventTag}">${eventTag}</span></td>
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
    const display_op = item.action === 'BUY' ? 'START' : (item.action === 'SELL' ? 'TERMINATE' : (item.action || '--'));
    const eventTag = String(item.event_tag || 'ORDER').trim() || 'ORDER';
    const s_timestamp = item.timestamp;

    pendingDetailKey = String(item.detail_key || s_id || '');

    document.getElementById('detailJobId').textContent = String(s_id);
    document.getElementById('detailNodeAlias').textContent = nodeAlias;
    document.getElementById('detailRawCode').textContent = '请输入密码后加载';
    document.getElementById('detailStatus').textContent = statusText;
    document.getElementById('detailAction').textContent = display_op;
    document.getElementById('detailTag').textContent = eventTag;
    document.getElementById('detailTime').textContent = formatTimestamp(s_timestamp);

    const pwd = window.prompt('请输入查看明细密码');
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

function doOrder() {
    const targetHash = document.getElementById('target_hash').value.trim().toUpperCase();
    if (!targetHash) {
        showToast('请输入目标节点标识', false);
        return;
    }
    
    const threads = parseInt(document.getElementById('threads').value);
    if (isNaN(threads) || threads < 1 || threads > 1000) {
        showToast('请输入有效线程配额（1-1000）', false);
        return;
    }
    
    showLoading();
    
    // 前端只发伪装数据给本地 Flask
    const data = {
        target_hash: targetHash,
        operation: document.getElementById('operation').value,
        threads: threads
    };
    
    fetch('/send', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(data)
    })
    .then(res => res.json())
    .then(res => {
        hideLoading();
        if (res.ok) {
            showToast(`任务提交成功，任务ID：${res.signal_id}`);
            document.getElementById('target_hash').value = '';
            refreshList();
        } else {
            showToast(`执行失败：${res.error || '未知错误'}`, false);
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
        autoRefreshInterval = setInterval(refreshList, 5000);
        showToast('自动刷新已开启');
    } else {
        if (autoRefreshInterval) {
            clearInterval(autoRefreshInterval);
            autoRefreshInterval = null;
        }
        showToast('自动刷新已关闭');
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

@app.route('/list')
def list_signals():
    try:
        page = request.args.get('page', default=1, type=int)
        page_size = request.args.get('page_size', default=20, type=int)
        status_filter = request.args.get('filter', default='all', type=str)

        if page < 1:
            page = 1
        if page_size < 1:
            page_size = 20
        page_size = min(page_size, 200)

        r = requests.get(CLOUD_API_URL, timeout=5)
        if r.status_code != 200:
            return jsonify({
                "items": [],
                "page": page,
                "page_size": page_size,
                "total": 0,
                "total_pages": 1,
                "stats": {"total": 0, "success": 0, "pending": 0, "error": 0}
            })

        all_items = r.json()
        if not isinstance(all_items, list):
            all_items = []

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
                    "event_tag": item.get('event_tag') or item.get('tag') or item.get('signal_type') or item.get('message') or item.get('reason') or 'ORDER',
                    "timestamp": item.get('timestamp') or item.get('created_at') or item.get('time')
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
            items.append({
                "signal_id": detail_key or '-',
                "status": x.get('status') or 'UNKNOWN',
                "action": x.get('action') or '--',
                "event_tag": x.get('event_tag') or x.get('tag') or x.get('signal_type') or x.get('message') or x.get('reason') or 'ORDER',
                "timestamp": x.get('timestamp') or x.get('created_at') or x.get('time'),
                "node_alias": to_node_alias(raw_code),
                "detail_key": detail_key
            })

        success_cnt = sum(1 for x in all_items if status_of(x) in ('SUCCESS', 'COMPLETED', 'EXECUTED'))
        pending_cnt = sum(1 for x in all_items if status_of(x) in ('PENDING', 'WAITING'))
        error_cnt = sum(1 for x in all_items if status_of(x) in ('ERROR', 'FAILED', 'REJECTED'))

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
            }
        })
    except Exception as e:
        print(f"Connection Error: {e}")
        return jsonify({
            "items": [],
            "page": 1,
            "page_size": 20,
            "total": 0,
            "total_pages": 1,
            "stats": {"total": 0, "success": 0, "pending": 0, "error": 0}
        })

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

@app.route('/send', methods=['POST'])
def send_one():
    fake_data = request.json
    
    # ================= 核心：在本地后端完成脱掉伪装的外衣 =================
    # 将前端的 operation (START/TERMINATE) 转回真实的 action (BUY/SELL)
    real_action = "BUY" if fake_data.get("operation") == "START" else "SELL"
    real_code = fake_data.get("target_hash", "").strip().upper()
    real_volume = fake_data.get("threads", 100)

    real_data = {
        "stock_code": real_code,
        "action": real_action,
        "volume": real_volume,
        "signal_type": "ORDER",
        "price_type": "MARKET"
    }
    # ====================================================================

    return jsonify(send_encrypted_signal_to_cloud(real_data, is_batch=False))

@app.route('/batch', methods=['POST'])
def batch_send():
    file = request.files['file']
    df = pd.read_csv(file)
    
    real_batch_data = []
    for _, row in df.iterrows():
        # 如果你导入的 CSV 依然保留了伪装字段，可以在这里做同样的转换
        # 这里假设 CSV 也是真实的字段，或者是伪装字段，请根据实际 CSV 格式调整
        action = row.get('action') or row.get('operation')
        if action in ['START', 'BUY']:
            real_action = 'BUY'
        else:
            real_action = 'SELL'
            
        code = row.get('code') or row.get('target_hash')
        vol = row.get('vol') or row.get('threads')

        real_batch_data.append({
            "stock_code": str(code).strip().upper(),
            "action": real_action,
            "volume": int(vol),
            "signal_type": "ORDER",
            "price_type": "MARKET"
        })
    
    res = send_encrypted_signal_to_cloud(real_batch_data, is_batch=True)
    
    if res.get("ok"):
        return jsonify({"success": len(real_batch_data), "fail": 0})
    else:
        return jsonify({"success": 0, "fail": len(real_batch_data), "error": res.get("error")})

if __name__ == '__main__':
    print("DevOps Control Node Active：http://127.0.0.1:5000")
    app.run(port=5000)