# Windows 正式上线手册（统一入口）

## 一、最终运行架构（正式口径）

- `EMS_API`：Windows 服务（NSSM 托管）
- `EMS_MAIL_INGEST`：Windows 服务（可选启用）
- `EMS_RUNNER`：**不作为服务**，必须在 Ptrade 客户端策略环境中运行（通过桥接脚本）

> 结论：正式上线请始终用 `scripts/windows/go_live.ps1` 作为总入口。

---

## 二、保留的正式脚本

### 统一入口（推荐）
- `scripts/windows/go_live.ps1`：一键串行执行“填配置 -> 部署 -> 检查”

### 受入口调用的核心脚本
- `scripts/windows/fill_config.ps1`：交互生成 `src/config.py`
- `scripts/windows/deploy_one_click.ps1`：安装依赖、执行迁移、安装并启动服务
- `scripts/windows/verify_checklist.ps1`：部署后健康检查

### 运维脚本（按需）
- `scripts/windows/install_services.ps1`：手工重装服务
- `scripts/windows/uninstall_services.ps1`：卸载服务

### 业务脚本（保留）
- `scripts/run_migrations.py`
- `scripts/api_server.py`
- `scripts/email_ingest_runner.py`
- `scripts/ptrade_bridge_template.py`
- `scripts/send_order_api.py`
- `scripts/submit_order_cli.py`
- `scripts/run_ems.py`（本地调试入口，非正式服务）

---

## 三、正式上线步骤（你现在就按这个跑）

在管理员 PowerShell 中执行：

```powershell
cd "E:\软件\cursorregister\trader"
powershell -ExecutionPolicy Bypass -File scripts\windows\go_live.ps1 -ProjectRoot "E:\软件\cursorregister\trader" -PythonExe "python" -NssmExe "nssm" -EnableMailService
```

如果暂时不启用邮件服务，去掉 `-EnableMailService` 即可。

### 可选参数

- 跳过配置向导（你已经配置好时）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\go_live.ps1 -ProjectRoot "E:\软件\cursorregister\trader" -SkipConfigWizard
```

- 跳过 pip 安装（依赖已完整安装时）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\go_live.ps1 -ProjectRoot "E:\软件\cursorregister\trader" -SkipPipInstall
```

---

## 四、上线后验证（必须）

`go_live.ps1` 已自动做一次检查。你也可以手工复查：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\verify_checklist.ps1 -ProjectRoot "E:\软件\cursorregister\trader" -ApiUrl "http://127.0.0.1:18080/health" -MySqlHost "127.0.0.1" -MySqlPort 3306 -DeepConfigCheck -CheckSystemTuning
```

---

## 五、Ptrade 必做步骤（决定是否真正能跑通）

1. 登录 Ptrade 客户端
2. 打开策略编辑器
3. 复制 `scripts/ptrade_bridge_template.py` 内容
4. 确认脚本内 `PROJECT_ROOT` 路径正确
5. 点击运行（必须）

> 没有这一步，API 收到信号也不会执行下单。

---

## 六、联调测试下单

```powershell
python scripts\send_order_api.py --url "http://<云服务器IP>:18080/signals" --auth_mode hmac --api_key "strategy01" --api_secret "<你的hmac_secret>" --stock_code "600519.SH" --signal_type ORDER --action BUY --volume 100 --price_type MARKET
```

返回 `ok=true` 且有 `signal_id`，并在日志/数据库/Ptrade 侧看到对应动作，才算链路打通。

---

## 七、故障排查优先级

1. `logs/ems_api.err.log`
2. `logs/ems_mail.err.log`
3. `verify_checklist.ps1` 的 FAIL 项
4. Ptrade 策略日志（桥接脚本是否在运行）
