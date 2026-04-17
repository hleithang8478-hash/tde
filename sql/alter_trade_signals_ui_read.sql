-- 面板 OCR 查询：EMS 点击底栏 Tab → 截取 panel_content_bbox → Tesseract → 写入 ui_ocr_text
ALTER TABLE `trade_signals`
  MODIFY COLUMN `signal_type` ENUM('TARGET','ORDER','UI_READ') NOT NULL
    COMMENT 'TARGET=目标仓位, ORDER=直接下单, UI_READ=底栏面板OCR',
  ADD COLUMN `ui_panel` VARCHAR(32) NULL COMMENT 'orders|trades|funds|positions' AFTER `signal_type`,
  ADD COLUMN `ui_ocr_text` MEDIUMTEXT NULL COMMENT 'UI_READ 成功后的 OCR 全文' AFTER `last_error`;
