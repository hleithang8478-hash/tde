# Windows 正式上线手册（统一入口）

## 一、最终运行架构（正式口径）

- `EMS_API`：Windows 服务（NSSM 托管）
- `EMS_MAIL_INGEST`：Windows 服务（可选启用）
- `EMS_RUNNER`：**不作为服务**，在 **与券商桌面客户端同机** 的会话中运行：`python scripts\run_ems.py`（RPA 适配器会操作该客户端窗口）

> 结论：正式上线请始终用 `scripts/windows/go_live.ps1` 作为总入口。

---

## 二、保留的正式脚本

### 统一入口（推荐）
- `scripts/windows/go_live.ps1`：一键串行执行“填配置 -> 部署 -> 检查”

### 受入口调用的核心脚本
- `scripts/windows/fill_config.ps1`：转调 `scripts/configure_interactive.py`（**中文提问**）生成 `src/config.py`；可加 **`-LegacyConfigPath`** 先合并旧 `config.py` 再向导
- `scripts/merge_legacy_config.py`：仅把旧 `src/config.py` 中已有键合并进本工程当前结构（备份后覆盖）
- `scripts/configure_interactive.py`：跨平台一步式配置；支持 **`--merge-legacy`** / **`--merge-only`**
- `scripts/RUN_EMS_执行步骤.md`：执行端逐步说明（库表、配置、`run_ems.py`）
- `scripts/windows/deploy_one_click.ps1`：安装依赖、执行迁移、安装并启动服务
- `scripts/windows/verify_checklist.ps1`：部署后健康检查

### 运维脚本（按需）
- `scripts/windows/install_services.ps1`：手工重装服务
- `scripts/windows/uninstall_services.ps1`：卸载服务

### 业务脚本（保留）
- `scripts/run_migrations.py`
- `scripts/api_server.py`
- `scripts/email_ingest_runner.py`
- `scripts/submit_order_cli.py`（本机写库调试）
- `ems_commander.py`（生产向云端发遥测加密单）
- `scripts/run_ems.py`（**RPA 执行器**：本机与券商客户端同机运行）

---

## 三、正式上线步骤（你现在就按这个跑）

在管理员 PowerShell 中执行（将 `ProjectRoot` 换成你的工程路径）：

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

## 五、RPA 必做步骤（纯鼠标递交版）

1. **（升级时）** 若有旧工程 `config.py`：先 `python scripts\merge_legacy_config.py --legacy "旧路径\src\config.py"`，再跑配置向导补 `mf_*` 等；或 `fill_config.ps1 -LegacyConfigPath "..."`。
2. 登录券商 **Windows 桌面交易客户端**；库表执行 `sql/create_trade_signals.sql` 及 `sql/alter_trade_signals_quantity.sql`（若用数量模式字段）。
3. 在 `src/config.py` 的 `RPA_CONFIG` 中至少配置：`broker_process_name`、`main_window_title_re`、`order_ui_mouse_flow=True`、全套 **`mf_*` 坐标**、`confirm_button_xy` / `confirm_sell_button_xy`、`secondary_confirm_xy`、`window_maximize_button_xy`（可选）；`EXECUTOR_SUBMIT_ONLY_MODE` 控制是否只递交不轮询成交。
4. 在与客户端 **同一用户桌面会话** 中启动执行器：

```powershell
python scripts\run_ems.py
```

> 详见仓库内 `scripts/RUN_EMS_执行步骤.md`。未登录客户端 + 未配齐鼠标坐标，信号无法安全执行。

---

## 六、联调测试下单

- **与现网 API 一致**：使用 **`ems_commander.py`** 向 `http://<云服务器IP>:18080/v1/agent/telemetry/beat` 发送加密遥测包（HMAC 头 + Fernet 内层业务 JSON），响应含 `signal_id` 即入库成功。
- **仅测执行器**：在本机 **`python scripts/submit_order_cli.py`** 写入 `trade_signals`，再在券商同机运行 **`python scripts/run_ems.py`**。

返回 `ok=true` 且有 `signal_id`（或库中 `PENDING` 被消费），且券商客户端委托一致，即链路打通。

---

## 七、故障排查优先级

1. `logs/ems_api.err.log`
2. `logs/ems_mail.err.log`
3. `verify_checklist.ps1` 的 FAIL 项
4. `logs/error_shots/` 下 RPA 异常全屏截图；以及 `run_ems.py` 控制台输出
