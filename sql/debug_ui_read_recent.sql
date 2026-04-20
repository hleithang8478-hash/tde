-- 发完 U0 后在 EMS 同一库执行，看是否入库、是否被 EMS 跑完

-- 1) 最近 10 条任意信号（确认监察台是否真的写入库）
SELECT signal_id, signal_type, status, stock_code, ui_panel,
       LEFT(COALESCE(last_error,''), 120) AS err_preview,
       create_time, update_time
FROM trade_signals
ORDER BY signal_id DESC
LIMIT 10;

-- 2) 只看 UI_READ（面板 OCR）
SELECT signal_id, status, ui_panel,
       CHAR_LENGTH(COALESCE(ui_ocr_text,'')) AS ocr_chars,
       LEFT(COALESCE(ui_ocr_text,''), 300) AS ocr_preview,
       LEFT(COALESCE(last_error,''), 200) AS last_error,
       create_time, update_time
FROM trade_signals
WHERE signal_type = 'UI_READ'
ORDER BY signal_id DESC
LIMIT 15;

-- 3) 若某条一直 PENDING：看是否 EMS 没在跑、或连错库
--    把下面的 ? 换成上一步的 signal_id
-- SELECT * FROM trade_signals WHERE signal_id = ?;
