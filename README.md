# A股自动交易执行系统（EMS）- Ptrade 执行端

本项目用于在腾讯云 Windows Server + 山西证券 Ptrade 客户端环境中执行外部策略信号。
系统只负责交易执行，不负责策略生成。

## 正式上线统一入口

请直接使用：`scripts/windows/go_live.ps1`

它会按顺序执行：
1. 配置填写（`fill_config.ps1`）
2. 一键部署（`deploy_one_click.ps1`，含可选迁移）
3. 部署检查（`verify_checklist.ps1`）

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
│  │  └─ ptrade_adapter.py
│  ├─ core/
│  │  ├─ __init__.py
│  │  ├─ repository.py
│  │  └─ executor.py
│  └─ main.py
└─ scripts/
   ├─ api_server.py
   ├─ email_ingest_runner.py
   ├─ ptrade_bridge_template.py
   ├─ run_ems.py
   ├─ run_migrations.py
   ├─ send_order_api.py
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
- `EMS_RUNNER` 不作为服务，必须在 Ptrade 客户端策略环境中运行。
- Ptrade 桥接脚本模板：`scripts/ptrade_bridge_template.py`
- 实际在 Ptrade 中运行：`scripts/ptrade_bridge_generated.py`（由 `scripts/windows/fill_config.ps1` 自动生成）

---

## 通知模式配置 (NOTIFY_MODE)

系统现已支持灵活的通知路由，在 `src/config.py` 中配置：

- **NONE**: 静默模式。执行结果仅在本地日志 (`logs/ems_api.out.log`) 中打印，不进行外部推送。
- **EMAIL**: 邮件模式。需同时配置 `SMTP_CONFIG`（发件服务器、端口、账号密码及收件人列表）。系统会在订单开始、成功、失败或触发重试时，实时发送邮件通知。

---

## 联调发单示例

```powershell
python scripts\send_order_api.py --url "http://<云服务器IP>:18080/signals" --auth_mode hmac --api_key "strategy01" --api_secret "<你的hmac_secret>" --stock_code "600519.SH" --signal_type ORDER --action BUY --volume 100 --price_type MARKET
```

更多参数和运维说明请看：`scripts/windows/README_SERVICES.md`
