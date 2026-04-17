-- 数量模式：ABSOLUTE=股数(volume)；TARGET_PCT=目标仓位/比例，用 weight_pct（如 25 表示 25%）
ALTER TABLE `trade_signals`
  ADD COLUMN `quantity_mode` VARCHAR(20) NOT NULL DEFAULT 'ABSOLUTE'
    COMMENT 'ABSOLUTE=绝对股数, TARGET_PCT=目标仓位百分比' AFTER `volume`,
  ADD COLUMN `weight_pct` DECIMAL(12, 6) NULL COMMENT 'quantity_mode=TARGET_PCT 时必填，百分比数值' AFTER `quantity_mode`;
