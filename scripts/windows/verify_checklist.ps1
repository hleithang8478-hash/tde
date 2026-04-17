# -*- coding: utf-8 -*-
# EMS 部署后自检（多行 UTF-8，避免旧版单行脚本编码损坏导致无法解析）

Param(
    [string]$ProjectRoot = "",
    [string]$ApiUrl = "http://127.0.0.1:18080/health",
    [string]$MySqlHost = "127.0.0.1",
    [int]$MySqlPort = 3306,
    [string]$MySqlIniPath = "",
    [string]$ExpectedServerPublicIp = "",
    [string]$ExpectedStrategyIp = "",
    [switch]$DeepConfigCheck,
    [switch]$CheckSystemTuning
)

$ErrorActionPreference = "Continue"

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}

$Script:PassCount = 0
$Script:WarnCount = 0
$Script:FailCount = 0

function Pass([string]$msg) {
    $Script:PassCount++
    Write-Host "[PASS] $msg" -ForegroundColor Green
}

function Warn([string]$msg) {
    $Script:WarnCount++
    Write-Host "[WARN] $msg" -ForegroundColor Yellow
}

function Fail([string]$msg) {
    $Script:FailCount++
    Write-Host "[FAIL] $msg" -ForegroundColor Red
}

function Section([string]$title) {
    Write-Host ""
    Write-Host "=== $title ===" -ForegroundColor Cyan
}

Section "0) 路径与关键文件"
if (Test-Path -LiteralPath $ProjectRoot) {
    Pass "项目目录存在: $ProjectRoot"
}
else {
    Fail "项目目录不存在: $ProjectRoot"
}

$criticalFiles = @(
    "src\config.py",
    "src\adapters\rpa_trade_adapter.py",
    "scripts\api_server.py",
    "scripts\configure_interactive.py",
    "scripts\merge_legacy_config.py",
    "scripts\run_ems.py",
    "src\rpa\window_controller.py",
    "scripts\windows\go_live.ps1",
    "scripts\windows\deploy_one_click.ps1",
    "sql\create_trade_signals.sql"
)

foreach ($f in $criticalFiles) {
    $full = Join-Path $ProjectRoot $f
    if (Test-Path -LiteralPath $full) { Pass "文件存在: $f" }
    else { Fail "缺少文件: $f" }
}

Section "1) Windows 服务"
$services = @("EMS_API", "EMS_MAIL_INGEST")
foreach ($svcName in $services) {
    $svc = Get-Service -Name $svcName -ErrorAction SilentlyContinue
    if ($null -eq $svc) {
        Warn "服务未安装: $svcName"
    }
    elseif ($svc.Status -eq "Running") {
        Pass "服务运行中: $svcName"
    }
    else {
        Warn "服务状态非 Running: $svcName => $($svc.Status)"
    }
}

$runnerSvc = Get-Service -Name "EMS_RUNNER" -ErrorAction SilentlyContinue
if ($null -ne $runnerSvc) {
    Warn "检测到 EMS_RUNNER 服务（RPA 模式通常不安装该服务）"
}
else {
    Pass "未检测到 EMS_RUNNER 服务（符合 RPA：本机 python scripts\run_ems.py）"
}

Section "2) API 与网络"
try {
    $resp = Invoke-WebRequest -Uri $ApiUrl -UseBasicParsing -TimeoutSec 5
    if ($resp.StatusCode -eq 200) {
        Pass "API 健康检查通过: $ApiUrl"
    }
    else {
        Fail "API 返回非 200: $($resp.StatusCode)"
    }
}
catch {
    Fail "API 无法访问: $ApiUrl"
}

try {
    $tnc = Test-NetConnection -ComputerName $MySqlHost -Port $MySqlPort -WarningAction SilentlyContinue
    if ($tnc.TcpTestSucceeded) {
        Pass "MySQL 端口可达: ${MySqlHost}:${MySqlPort}"
    }
    else {
        Fail "MySQL 端口不可达: ${MySqlHost}:${MySqlPort}"
    }
}
catch {
    Warn "无法执行 Test-NetConnection，跳过 MySQL 端口检测"
}

if ($ExpectedServerPublicIp) {
    try {
        $actualIp = (Invoke-WebRequest -Uri "https://ifconfig.me/ip" -UseBasicParsing -TimeoutSec 6).Content.Trim()
        if ($actualIp -eq $ExpectedServerPublicIp) {
            Pass "公网 IP 符合预期: $actualIp"
        }
        else {
            Warn "公网 IP 与预期不一致，实际=$actualIp 预期=$ExpectedServerPublicIp"
        }
    }
    catch {
        Warn "无法获取公网 IP（可忽略）"
    }
}

Section "3) 配置安全检查"
$configPath = Join-Path $ProjectRoot "src\config.py"
if (Test-Path -LiteralPath $configPath) {
    $cfg = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8
    if ($cfg -match "change_me_to_a_strong_token") {
        Fail "API token 仍是默认占位"
    }
    else {
        Pass "API token 看似已修改"
    }
    if ($cfg -match "change_me_to_a_very_strong_secret") {
        Fail "HMAC secret 仍是默认占位"
    }
    else {
        Pass "HMAC secret 看似已修改"
    }
    if ($cfg -match "your_user" -or $cfg -match "your_password" -or $cfg -match "your_db") {
        Fail "DB_CONFIG 仍含占位符"
    }
    else {
        Pass "DB_CONFIG 看似已填写"
    }

    if ($cfg -match '"hmac_enabled"\s*:\s*True') {
        Pass "已启用 HMAC 鉴权"
    }
    else {
        Warn "未检测到 hmac_enabled=True"
    }

    # 常见错误：JSON 小写 true/false 会导致 Python 报错（须用 -cmatch，避免把合法的 False 误判）
    if ($cfg -cmatch ':\s*false\b' -or $cfg -cmatch ':\s*true\b') {
        Fail "config.py 中疑似使用 JSON 小写 true/false，Python 需要 True/False。请重新运行 scripts/configure_interactive.py（或 fill_config.ps1）或手工改正。"
    }
    else {
        Pass "未发现 JSON 风格小写布尔值"
    }

    if ($ExpectedStrategyIp) {
        if ($cfg -match [Regex]::Escape($ExpectedStrategyIp)) {
            Pass "配置中检测到策略机 IP 白名单: $ExpectedStrategyIp"
        }
        else {
            Warn "未在 config.py 中检测到策略机 IP: $ExpectedStrategyIp"
        }
    }
}
else {
    Fail "找不到配置文件: src\config.py"
}

Section "4) MySQL 参数（可选）"
if ($MySqlIniPath -and (Test-Path -LiteralPath $MySqlIniPath)) {
    $myini = Get-Content -LiteralPath $MySqlIniPath -Raw
    if ($myini -match "innodb_buffer_pool_size\s*=\s*512M") {
        Pass "my.ini 已配置 innodb_buffer_pool_size=512M"
    }
    else {
        Warn "my.ini 未检测到 innodb_buffer_pool_size=512M"
    }
    if ($myini -match "max_connections\s*=\s*50") {
        Pass "my.ini 已配置 max_connections=50"
    }
    else {
        Warn "my.ini 未检测到 max_connections=50"
    }
}
else {
    Warn "未提供 MySqlIniPath，跳过 my.ini 参数检查"
}

Section "5) 日志与运行痕迹"
$logFiles = @(
    "logs\ems_api.out.log",
    "logs\ems_api.err.log",
    "logs\ems_mail.out.log",
    "logs\ems_mail.err.log"
)
foreach ($lf in $logFiles) {
    $p = Join-Path $ProjectRoot $lf
    if (Test-Path -LiteralPath $p) {
        Pass "日志文件存在: $lf"
    }
    else {
        Warn "日志文件不存在: $lf"
    }
}

Section "6) RPA 适配器"
$rpaPath = Join-Path $ProjectRoot "src\adapters\rpa_trade_adapter.py"
if (Test-Path -LiteralPath $rpaPath) {
    $rpa = Get-Content -LiteralPath $rpaPath -Raw -Encoding UTF8
    if ($rpa -match "class RpaTradeAdapter" -and $rpa -match "def place_order") {
        Pass "RPA 适配器文件存在且含核心类"
    }
    else {
        Warn "RPA 适配器内容可能不完整"
    }
    Warn "无法自动确认券商客户端已登录且 RPA 坐标已配置（需人工确认）"
}
else {
    Fail "缺少 RPA 适配器文件"
}

if ($CheckSystemTuning) {
    Section "7) 系统调优（可选）"
    try {
        $visual = Get-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects" -ErrorAction SilentlyContinue
        if ($null -ne $visual -and $visual.VisualFXSetting -eq 2) {
            Pass "视觉效果已设为最佳性能（当前用户）"
        }
        else {
            Warn "未确认视觉效果=最佳性能（请手工确认）"
        }
    }
    catch {
        Warn "无法读取视觉效果配置"
    }

    try {
        $page = Get-CimInstance Win32_PageFileSetting -ErrorAction SilentlyContinue
        if ($null -eq $page) {
            Warn "未读取到分页文件设置（可能系统自动管理）"
        }
        else {
            $matched = $false
            foreach ($it in $page) {
                if (($it.InitialSize -ge 8192 -and $it.InitialSize -le 16384) -or ($it.MaximumSize -ge 8192 -and $it.MaximumSize -le 16384)) {
                    $matched = $true
                }
            }
            if ($matched) {
                Pass "检测到虚拟内存配置在建议范围（8~16GB）"
            }
            else {
                Warn "未检测到建议的虚拟内存配置（8~16GB）"
            }
        }
    }
    catch {
        Warn "无法读取虚拟内存配置"
    }
}

Section "汇总"
Write-Host "通过: $($Script:PassCount)" -ForegroundColor Green
Write-Host "警告: $($Script:WarnCount)" -ForegroundColor Yellow
Write-Host "失败: $($Script:FailCount)" -ForegroundColor Red

if ($Script:FailCount -eq 0) {
    Write-Host "检查完成：无硬失败项。" -ForegroundColor Green
}
else {
    Write-Host "检查完成：存在失败项，建议先修复 [FAIL]。" -ForegroundColor Red
}

Write-Host "提醒：最关键人工项是「券商客户端已登录 + RPA_CONFIG 坐标/VLM 已配置 + 本机已运行 python scripts\run_ems.py」。" -ForegroundColor Yellow
