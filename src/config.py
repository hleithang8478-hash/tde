# -*- coding: utf-8 -*-
"""EMS 全局配置：数据库、API、邮件、执行器与 Windows RPA。改完需重启相关进程生效。"""

# ---------- 部署路径（NSSM / 脚本对照用，键名勿改）----------
DEPLOY_PATHS = {
    # 开发机本仓库根目录
    "development_project_root": "C:\\软件\\trader",
    # 服务器上同一项目根目录（与上相同可填一样）
    "server_project_root": "C:\\软件\\trader",
    # 服务器上 python 可执行文件；不确定可填 "python"
    "server_python_exe": "python",
    # NSSM 路径提示；不用 Windows 服务可占位 "nssm"
    "nssm_exe_hint": "nssm",
    # 日志相对工程根的子目录名
    "logs_dir_relative": "logs",
}

# ---------- MySQL（EMS 读写 trade_signals 等表）----------
DB_CONFIG = {
    # 数据库主机；本机 MySQL 常用 127.0.0.1
    "host": "127.0.0.1",
    # 端口，默认 3306
    "port": 3306,
    # 用户名
    "user": "root",
    # 密码
    "password": "Qwer!1234567",
    # 库名（须已执行 migrations 建表）
    "database": "mydb",
    # 字符集，固定 utf8mb4 即可
    "charset": "utf8mb4",
}

# 告警方式：NONE=不发邮件；EMAIL=走下方 SMTP
NOTIFY_MODE = "NONE"  # "NONE" or "EMAIL"

SMTP_CONFIG = {
    # 发件 SMTP 主机；不用邮件可留空
    "host": "",
    # SMTP 端口，SSL 常用 465
    "port": 465,
    "user": "",
    "password": "",
    # 收件人邮箱列表，如 ["a@qq.com"]
    "receivers": [],
}

# 主循环轮询间隔（秒）
POLL_INTERVAL_SECONDS = 5
# 单笔信号最大重试次数
MAX_RETRY = 5
# 每轮最多取几条待处理信号
BATCH_SIZE = 10
# 等待委托终态的超时（秒）；EXECUTOR 仅递交模式时意义较弱
ORDER_WAIT_TIMEOUT = 120
# 查单轮询间隔（秒）
ORDER_POLL_INTERVAL = 1

# True：下单 API 返回后直接把信号标 SUCCESS，不轮询柜台 query_order（纯递交）
EXECUTOR_SUBMIT_ONLY_MODE = True

# ---------- 邮件拉取信号（可选）----------
EMAIL_INGEST_CONFIG = {
    # 是否从邮箱拉 EMS 主题邮件入库
    "enabled": False,
    "imap_host": "imap.qq.com",
    "imap_port": 993,
    "username": "",
    "password": "",
    "folder": "INBOX",
    # 主题须以前缀开头才会被当作信号
    "subject_prefix": "EMS_SIGNAL",
    # 轮询邮箱间隔（秒）
    "poll_interval_seconds": 15,
}

# ---------- HTTP API（ems_commander / 外部发单连这里）----------
# HMAC 单一真源：api_server 读 API_CONFIG；ems_commander 优先 import 此处，与仅拷贝单文件时的回退字面值保持一致。
EMS_HMAC_KEY_ID_DEFAULT = "strategy01"
EMS_HMAC_SECRET_DEFAULT = "Qwer!1234567"

API_CONFIG = {
    # 监听地址；云上对外服务常用 0.0.0.0
    "host": "0.0.0.0",
    "port": 18080,
    # 简单 token；与 commander 配置一致；关 HMAC 时仍建议填
    "token": "",
    # 允许访问 API 的客户端 IP 列表，本机测试须含 127.0.0.1
    "ip_whitelist": ["114.255.150.162","127.0.0.1"],
    # 是否校验 HMAC 签名
    "hmac_enabled": True,
    # key_id -> secret（由上方常量生成，勿与 ems_commander 分叉维护）
    "hmac_keys": {
        EMS_HMAC_KEY_ID_DEFAULT: EMS_HMAC_SECRET_DEFAULT,
    },
    # 请求时间戳允许偏差（秒）
    "max_skew_seconds": 300,
    # nonce 防重放缓存时间（秒）
    "nonce_ttl_seconds": 300,
}

# ---------- Windows 券商客户端 RPA（PTrade 等）----------
RPA_CONFIG = {
    # 券商 exe 全路径；留空则只检测进程、不自动双击启动（推荐生产）
    "broker_exe_path": "",
    # 进程名，任务管理器「名称」列，如 ptrade.exe
    "broker_process_name": "ptrade.exe",
    # 是否在未检测到进程时尝试自动启动 exe
    "broker_auto_start": True,
    # 自动启动后等待秒数
    "broker_start_wait_sec": 3.0,
    # 主窗口标题匹配用正则（与 Spy++ 标题一致的一段）
    "main_window_title_re": "PTrade.*交易",
    # pywinauto 后端，一般 uia
    "pywinauto_backend": "uia",
    # 钉钉机器人 Webhook；不用留空
    "dingtalk_webhook_url": "",
    # VLM 完整 Chat URL（…/v1/chat/completions）；不用留空
    "vlm_api_url": "",
    "vlm_api_key": "",
    "vlm_model": "deepseek-chat",
    # auto：无图走 OCR+文本；vision：需支持 image_url 的接口
    "vlm_chat_mode": "auto",
    # Tesseract 语言：简体中文 + 英文（证券代码等）。必须安装 chi_sim.traineddata，
    # 否则会静默回退 eng，识别结果会像随机英文字母。可用命令验证：tesseract --list-langs
    "vlm_tesseract_lang": "chi_sim+eng",
    # tesseract.exe 绝对路径；PATH 能找到则留空
    "vlm_tesseract_cmd": "",
    # OCR 前是否做预处理
    "vlm_tesseract_preprocess": True,
    # Tesseract 额外参数（会与多策略里的 psm 合并；另见下方 -c 开关）
    "vlm_tesseract_config": "--oem 3 --psm 6",
    # 追加到每条 tesseract config 后的原始片段（例如自定义 -c）
    "vlm_tesseract_extra_args": "",
    # True：加 -c preserve_interword_spaces=1，利于中英文混排间距（交易表格强烈建议开）
    "vlm_tesseract_preserve_spaces": True,
    # False：加 -c tessedit_do_invert=0（深色背景浅色字时可试 True 配合反色）
    "vlm_tesseract_do_invert": False,
    # 仅允许出现的字符（极长中文白名单慎用）；勿含空格，勿含「=」
    "vlm_tesseract_char_whitelist": "",
    # True：二值化前做 OpenCV 轻微纠斜（持仓/委托表常微歪，建议开）
    "vlm_tesseract_preprocess_deskew": True,
    # 将截图短边放大到至少该像素（None 表示沿用内置「过小则 2 倍」逻辑）
    # 1200 经验值：交易表小字一般能从「11~14px」放大到 ~30px，明显提升识别率
    "vlm_tesseract_preprocess_target_min_edge": 1200,
    # 下单前 OCR 字符数低于此可能不调 LLM
    "ai_verify_min_ocr_signal": 14,
    # 是否在下单前对 bbox_pre_trade 区域做截图校验（须配 bbox）
    "ai_verify_enabled": False,
    # True：弹窗 VLM 解析失败则阻断
    "strict_popup_ai": False,
    # 标题栏「最大化」按钮中心 (x,y)；None 则不点最大化
    "window_maximize_button_xy": (1863, 21),
    # 点最大化后等待秒数
    "after_maximize_wait_sec": 0.3,
    # True：纯鼠标链填单（须配 mf_*）；False：走旧 Tab 路径
    "order_ui_mouse_flow": True,
    # True：下单后再扫委托表同步柜台单号；递交模式常配 False
    "rpa_submit_sync_after_fill": False,
    # 鼠标各步之间点击间隔（秒）；与方向多击无关
    "mf_click_pause_sec": 0.5,
    # 买卖方向勾选/单选：1=单击；2=同坐标连点两次（易选中难点的控件）；仍不稳可试 3
    "mf_direction_click_count": 2,
    # 同一次「方向」多击时，两次点击之间的间隔（秒），略大更利于柜台识别为两次
    "mf_direction_between_click_sec": 0.12,
    # 证券代码输入框中心
    "mf_stock_code_xy": (242, 884),
    # 「买入」方向控件中心
    "mf_direction_buy_xy": (153, 940),
    # 「卖出」方向控件中心
    "mf_direction_sell_xy": (239, 943),
    # 限价/市价若为两个独立按钮时用；使用下拉时可保持 None
    "mf_entrust_type_limit_xy": None,
    "mf_entrust_type_market_xy": None,
    # 委托类型下拉框点击处（配了则走下拉，忽略上一行两个）
    "mf_entrust_type_dropdown_xy": (268, 984),
    # 下拉展开后「限价」项中心
    "mf_entrust_type_limit_option_xy": (260, 1018),
    # 下拉展开后「市价」项中心
    "mf_entrust_type_market_option_xy": (250, 1136),
    # 点开委托类型下拉后等待秒数再点选项
    "mf_entrust_type_dropdown_open_wait_sec": 0.25,
    # 委托价格输入框中心（限价必填；市价且跳过价格时可不配）
    "mf_price_xy": (279, 1029),
    # 市价时是否跳过价格框点击与输入
    "mf_skip_price_when_market": True,
    # 数量类型下拉框点击处
    "mf_quantity_type_dropdown_xy": (267, 1082),
    # 展开后「固定数量」项（对应信号 quantity_mode=ABSOLUTE）
    "mf_quantity_type_fixed_qty_option_xy": (273, 1117),
    # 展开后「固定金额」项（预留 UI）
    "mf_quantity_type_fixed_amount_option_xy": (273, 1146),
    # 展开后「持仓比例」项（对应 TARGET_PCT）
    "mf_quantity_type_position_ratio_option_xy": (269, 1203),
    # 数量下拉展开后等待秒数
    "mf_quantity_type_dropdown_open_wait_sec": 0.25,
    # 未用下拉时：「按股数」入口；与下拉二选一
    "mf_quantity_type_shares_xy": None,
    # 未用下拉时：「比例」入口
    "mf_quantity_type_ratio_xy": None,
    # 股数/比例两个入口都配时，True 优先点比例入口
    "mf_default_quantity_is_ratio": False,
    # 数量/百分比最终输入框中心
    "mf_quantity_xy": (271, 1167),
    # True：鼠标流结束后不按 Esc（减少键盘依赖）
    "order_mouse_flow_suppress_post_escape": True,
    # 下单前面板区域 [左,上,宽,高]，供 VLM 校验；关 ai_verify 可仍填作参考
    "bbox_pre_trade": (62, 859, 369, 439),
    # 大「买入」确认按钮中心；OpenCV 可辅助定位
    "confirm_button_xy": (273, 1272),
    # 卖出主按钮与买入不同时单独配；None 则用买入坐标/模板兜底
    "confirm_sell_button_xy": None,
    # 下单后第一次二次确认「确定」中心
    "secondary_confirm_xy": (1354, 848),
    # 柜台提示「委托成功」等后再弹一层「确定」时点击；None 跳过
    "post_order_success_ok_xy": (1439, 748),
    # 上述按钮的 OpenCV 模板名（不含 .png），空则仅用坐标
    "opencv_post_success_ok_template": "",
    # 点「成功后再确定」前等待秒数（等弹窗动画）
    "before_post_order_success_ok_sec": 0.2,
    # 点完该确定后等待秒数
    "after_post_order_success_ok_wait_sec": 0.35,
    # 旧 Tab 流：市价是否跳过价格字段（鼠标流里配合 mf_skip_price_when_market）
    "market_skip_price_field": True,
    # 旧 Tab 流：代码后额外 Tab 次数；纯鼠标流可保持 0
    "tabs_after_code": 0,
    # 旧 Tab 流：市价跳过价格时额外 Tab 次数
    "tabs_market_skip_price": 1,
    # 当日委托表区域 [左,上,宽,高]，撤单/同步用
    "bbox_order_table": (714, 853, 495, 1834),
    # ---------- 底栏「委托/成交/资金/持仓」+ 同块内容区（程序内截图 OCR 用，四角换算 bbox）----------
    # Tab 中心整屏坐标；后续：点击切换 → 对 panel_content_bbox 截图 → 滚轮锚点处 scroll → OCR 入库
    "panel_tab_orders_xy": (742, 871),
    "panel_tab_trades_xy": (822, 871),
    "panel_tab_funds_xy": (903, 871),
    "panel_tab_positions_xy": (984, 871),
    # 内容区 [左,上,宽,高]：左上(699,889)、右上(2556,889)、左下(699,1348)、右下(2556,1348)
    "panel_content_bbox": (699, 889, 1857, 459),
    # 滚轮前鼠标移到此点（内容区中心附近），再 pyautogui.scroll
    "panel_content_scroll_anchor_xy": (1599, 1154),
    # 点击底栏 Tab 后等待界面稳定（秒），再截图 OCR
    "panel_after_tab_wait_sec": 0.45,
    # 读面板前是否将交易客户端置前（与 status_monitor_bring_front 独立，默认同为 True）
    "panel_read_bring_front": True,
    # 刷新委托列表快捷键
    "orders_refresh_hotkey": "F3",
    # 刷新成交快捷键
    "trades_refresh_hotkey": "F4",
    # 按 F3 后委托表稳定等待秒数
    "orders_table_settle_sec": 0.55,
    # 监控前是否 bring 主窗到前台
    "status_monitor_bring_front": True,
    # 全局「撤单」按钮中心；无模板时必填
    "withdraw_button_xy": (1183, 909),
    # OpenCV 模板注册表（一般留空，由 registry.json 合并）
    "opencv_templates": {},
    # 模板匹配阈值 0~1，越大越严
    "opencv_match_threshold": 0.82,
    # 模板搜索边距像素
    "opencv_search_margin": 48,
    # 模板结果与锚点坐标允许偏差像素，超过打日志
    "opencv_dead_warn_px": 50,
    # 买入确认按钮模板 PNG 名（在 src/rpa/assets/）
    "opencv_confirm_template": "buy_confirm_btn",
    # 卖出确认专用模板名；空则用买入模板
    "opencv_confirm_sell_template": "",
    # 撤单图标模板名
    "opencv_withdraw_template": "withdraw_btn",
    # 二次确认「确定」模板名
    "opencv_secondary_confirm_template": "secondary_confirm_btn",
    # 撤单行水平条带半高像素，用于行内找图标
    "opencv_withdraw_strip_half_height": 18,
    # 是否启用持仓查询（TARGET 等）
    "position_query_enabled": True,
    # 持仓表区域 [左,上,宽,高]
    "bbox_positions_table": (714, 853, 495, 1834),
    # 切到持仓页的键盘快捷键；无则配合 positions_tab_xy 点击
    "positions_tab_hotkey": "",
    # 持仓页签按钮中心
    "positions_tab_xy": (996, 873),
    # 切到持仓 tab 后等待秒数
    "after_positions_tab_wait_sec": 0.4,
    # 持仓表截图前稳定等待秒数
    "positions_table_settle_sec": 0.5,
    # 单笔 RPA 任务总超时（秒）
    # PyAutoGUI：True 会在「鼠标移到屏幕角触发 FailSafeException」急停。
    # 改为 False 关闭：U0/下单等流程涉及左上侧坐标，频繁误触；如需重新启用请改回 True
    # 或用环境变量 EMS_PYAUTOGUI_FAILSAFE=1（优先级高于此处）。
    "pyautogui_fail_safe": False,
    # 每次 pyautogui 操作后的额外停顿秒数（None 表示不改默认）
    "pyautogui_pause_sec": None,
    "rpa_task_timeout_sec": 600,
    # 旧 Tab 流复位后等待（秒）
    "after_reset_wait_sec": 0.35,
    # 点主确认后等待秒数
    "after_confirm_wait_sec": 0.6,
    # 点二次确认后等待秒数
    "after_secondary_confirm_wait_sec": 0.35,
    # 递交后同步柜台单号的最大尝试次数
    "post_submit_sync_attempts": 5,
    # 同步尝试间隔（秒）
    "post_submit_sync_interval_sec": 1.2,
    # 是否启用委托表定时监控
    "status_monitor_enabled": True,
    # 监控轮询间隔（秒）
    "status_poll_interval_sec": 10.0,
}
