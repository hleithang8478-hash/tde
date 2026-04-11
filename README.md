# A股自动交易执行系统（EMS）- Ptrade 执行端

本项目用于在腾讯云 Windows Server + 山西证券 Ptrade 客户端环境中执行外部策略信号。
系统只负责交易执行，不负责策略生成。

## 标准目录结构

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
│  │  └─ dingtalk_notifier.py
│  ├─ adapters/
│  │  ├─ __init__.py
│  │  └─ ptrade_adapter.py
│  ├─ core/
│  │  ├─ __init__.py
│  │  ├─ repository.py
│  │  └─ executor.py
│  └─ main.py
└─ scripts/
   └─ run_ems.py
```

## 1. 安装依赖

```bash
pip install -r requirements.txt
```

> 注：Ptrade 内置 Python 环境如无法直接 `pip`，请按券商环境要求手工安装 `pymysql`、`sqlalchemy`、`requests`。

## 2. 建表

在 MySQL 中执行：

```sql
source sql/create_trade_signals.sql;
```

## 3. 配置

编辑 `src/config.py`：
- MySQL 连接信息
- 钉钉 Webhook
- 轮询间隔、最大重试次数

## 4. 适配 Ptrade API

编辑 `src/adapters/ptrade_adapter.py` 中以下函数，替换成山西证券 Ptrade 真实 API：
- `get_position_volume`
- `place_order`
- `query_order`
- `cancel_order`

## 5. 启动

```bash
python scripts/run_ems.py
```

## 6. 运行逻辑概览

- 每 5 秒轮询一次 `trade_signals` 中 `PENDING` 信号。
- `ORDER` 模式：直接按 action + volume 下单。
- `TARGET` 模式：先查持仓，计算差额后决定买卖方向与数量。
- 对部分成交/未成交执行撤单 + 剩余补单重试。
- 超过最大重试次数标记 `FAILED`。
- 关键节点推送钉钉消息。
