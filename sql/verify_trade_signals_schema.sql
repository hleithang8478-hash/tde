-- 在 EMS 使用的库上执行，核对 trade_signals 是否支持 UI_READ（面板 OCR）
-- 用法示例：mysql -u... -p... mydb < sql/verify_trade_signals_schema.sql

-- 1) 表是否存在
SELECT COUNT(*) AS table_exists
FROM information_schema.tables
WHERE table_schema = DATABASE() AND table_name = 'trade_signals';

-- 2) 列：须含 ui_panel、ui_ocr_text、quantity_mode、weight_pct（后两者为历史 alter）
SELECT column_name, data_type, is_nullable, column_type
FROM information_schema.columns
WHERE table_schema = DATABASE() AND table_name = 'trade_signals'
ORDER BY ordinal_position;

-- 3) signal_type 枚举是否含 UI_READ（期望结果里能看到 UI_READ）
SELECT column_type
FROM information_schema.columns
WHERE table_schema = DATABASE()
  AND table_name = 'trade_signals'
  AND column_name = 'signal_type';

-- 4) 建表语句（人工扫一眼 ENUM / 列名即可）
SHOW CREATE TABLE trade_signals\G
