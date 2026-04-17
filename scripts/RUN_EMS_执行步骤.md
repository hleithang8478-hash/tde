# RPA 执行端（EMS Runner）操作顺序

在 **与 PTrade 等券商客户端同一台 Windows** 上，建议按下面顺序做一遍。

## 1. 数据库

- 建库建表：`sql/create_trade_signals.sql`
- 若使用「数量模式」等新字段：`sql/alter_trade_signals_quantity.sql`

## 2. 配置 `src/config.py`

**方式 A：从旧工程回填再补全（推荐升级时）**

```powershell
cd <本工程根目录>
python scripts/merge_legacy_config.py --legacy "D:\旧工程\trader\src\config.py"
# 可先 --dry-run 看合并结果
python scripts/configure_interactive.py --mode full
```

或一条里只合并不提问：

```text
python scripts/configure_interactive.py --merge-legacy "D:\旧工程\trader\src\config.py" --merge-only
```

**方式 B：全新配置**

```text
powershell -ExecutionPolicy Bypass -File scripts\windows\fill_config.ps1 -ProjectRoot "<根目录>"
```

## 3. 纯鼠标递交流程（当前默认）

执行器侧：`EXECUTOR_SUBMIT_ONLY_MODE = True` → 只负责把单递交到柜台，**不**轮询成交。

RPA 侧要点（`RPA_CONFIG`）：

1. `broker_process_name` / `main_window_title_re`：能稳定找到主窗  
2. `window_maximize_button_xy`（可选）：最大化  
3. `order_ui_mouse_flow = True`：鼠标链填单  
4. 配齐 `mf_*`：代码、买卖方向、限价/市价、价格框、数量类型（股数/比例）、数量输入框  
5. `confirm_button_xy` / `confirm_sell_button_xy`、`secondary_confirm_xy`：主按钮与弹窗确定  
6. `rpa_submit_sync_after_fill = False`：递交后不扫委托表（与「仅递交」一致）

## 4. 启动执行器

```text
python scripts/run_ems.py
```

保持该进程常驻；信号由 API 写入库或由 `submit_order_cli` 写入，本进程轮询 `PENDING` 并执行。

## 5. 联调发单（任选）

- 本机写库：`python scripts/submit_order_cli.py`（支持 `quantity_mode` / `weight_pct`）  
- 生产：`ems_commander.py` → 云端 API（密钥与 `api_server.py` 一致）

---

更多服务/NSSM 说明见：`scripts/windows/README_SERVICES.md`。
