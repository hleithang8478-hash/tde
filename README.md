# A股自动交易执行系统（EMS）- Windows RPA 执行端

本项目用于在 Windows Server 与券商**桌面交易客户端**同机部署，通过 **RPA（窗口 + 纯鼠标填单为主，可选 VLM/OCR 校验）** 执行外部策略信号。
系统只负责交易执行，不负责策略生成。

## 正式上线统一入口

请直接使用：`scripts/windows/go_live.ps1`

它会按顺序执行：
1. 配置填写（`fill_config.ps1` → `scripts/configure_interactive.py`：**快速 / 标准 / 完整**三档；**可选** `-LegacyConfigPath` 先从旧 `config.py` 合并再向导）
2. 一键部署（`deploy_one_click.ps1`，含可选迁移）
3. 部署检查（`verify_checklist.ps1`）

**RPA 执行端逐步说明**（迁移、库表、`run_ems.py`）：见 `scripts/RUN_EMS_执行步骤.md`。  
**单独合并旧配置**：`python scripts/merge_legacy_config.py --legacy "旧工程\src\config.py"`（可先 `--dry-run`）。

---

## 项目结构（当前正式版）

```text
trader/
├─ README.md
├─ requirements.txt
├─ sql/
│  └─ create_trade_signals.sql
├─ src/
│  ├─ __init__.py
│  ├─ config.py
│  ├─ notifier/
│  │  ├─ __init__.py
│  │  └─ system_notifier.py
│  ├─ adapters/
│  │  ├─ __init__.py
│  │  └─ rpa_trade_adapter.py
│  ├─ core/
│  │  ├─ __init__.py
│  │  ├─ repository.py
│  │  └─ executor.py
│  ├─ rpa/
│  │  ├─ __init__.py
│  │  ├─ window_controller.py
│  │  ├─ vision_inspector.py
│  │  ├─ rpa_input_engine.py
│  │  ├─ rpa_config_manager.py
│  │  └─ template_registry.py
│  └─ main.py
└─ scripts/
   ├─ api_server.py
   ├─ email_ingest_runner.py
   ├─ configure_interactive.py
   ├─ merge_legacy_config.py
   ├─ RUN_EMS_执行步骤.md
   ├─ run_ems.py
   ├─ run_migrations.py
   ├─ rpa_calibrate.py
   ├─ submit_order_cli.py
   └─ windows/
      ├─ go_live.ps1
      ├─ fill_config.ps1
      ├─ deploy_one_click.ps1
      ├─ verify_checklist.ps1
      ├─ install_services.ps1
      ├─ uninstall_services.ps1
      └─ README_SERVICES.md
```

---

## Windows 上线（建议按此执行）

在管理员 PowerShell：

```powershell
cd "E:\软件\cursorregister\trader"
powershell -ExecutionPolicy Bypass -File scripts\windows\go_live.ps1 -ProjectRoot "E:\软件\cursorregister\trader" -PythonExe "python" -NssmExe "nssm" -EnableMailService
```

如果不用邮件服务，去掉 `-EnableMailService`。

---

## 重要运行规则

- `EMS_API`、`EMS_MAIL_INGEST` 由 NSSM 托管为 Windows 服务。
- `EMS_RUNNER` 不作为服务；**RPA 模式**下在本机与券商客户端同机运行 `python scripts/run_ems.py`（需已配置 `src/config.py` 中 `RPA_CONFIG`，当前默认 **纯鼠标链** `order_ui_mouse_flow`，递交模式见 `EXECUTOR_SUBMIT_ONLY_MODE`）。

---

## 通知模式配置 (NOTIFY_MODE)

系统现已支持灵活的通知路由，在 `src/config.py` 中配置：

- **NONE**: 静默模式。执行结果仅在本地日志 (`logs/ems_api.out.log`) 中打印，不进行外部推送。
- **EMAIL**: 邮件模式。需同时配置 `SMTP_CONFIG`（发件服务器、端口、账号密码及收件人列表）。系统会在订单开始、成功、失败或触发重试时，实时发送邮件通知。

---

## 联调发单

- **生产路径**：云端 API 为遥测外形 + Fernet + HMAC，请使用仓库根目录 **`ems_commander.py`**（或与其一致的客户端）向 **`POST /v1/agent/telemetry/beat`** 发单；密钥须与 `scripts/api_server.py` 中 `ENCRYPTION_KEY` 一致。
- **本机直写库（调试用）**：`python scripts/submit_order_cli.py` 将信号写入 MySQL，无需加密。

更多说明：`scripts/windows/README_SERVICES.md`
