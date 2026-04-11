# Windows 服务化部署说明（NSSM）

## 1. 前置准备

1. 安装 Python（与项目依赖一致）
2. 安装 NSSM（https://nssm.cc/download）并确保 `nssm.exe` 在 PATH 中
3. 在项目根目录创建日志目录：

```powershell
mkdir logs
```

## 2. 安装依赖

```powershell
pip install -r requirements.txt
```

## 3. 修改配置

请先修改：
- `src/config.py` 中 `DB_CONFIG`
- `API_CONFIG` 的 `token` 或 `hmac_keys`
- `EMAIL_INGEST_CONFIG`（如不使用邮件可 `enabled=False`）

## 4. 安装并启动服务

在 PowerShell（管理员）执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\install_services.ps1 -PythonExe "python" -NssmExe "nssm" -ProjectRoot "E:\软件\cursorregister\trader"
```

## 5. 检查服务状态

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\check_services.ps1
```

## 6. 卸载服务

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\uninstall_services.ps1 -NssmExe "nssm"
```

## 7. 服务说明

- `EMS_API`：接收远程HTTP下单信号
- `EMS_RUNNER`：轮询数据库并执行交易（Ptrade端）
- `EMS_MAIL_INGEST`：轮询邮箱并入库信号

如果暂时不用邮件通道，可在安装后手动停止并禁用 `EMS_MAIL_INGEST`。
