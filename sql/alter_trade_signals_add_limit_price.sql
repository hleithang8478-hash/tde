ALTER TABLE `trade_signals`
ADD COLUMN `limit_price` DECIMAL(10,3) NULL COMMENT '限价委托价格，MARKET可为空' AFTER `price_type`;
