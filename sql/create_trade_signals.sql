CREATE TABLE IF NOT EXISTS `trade_signals` (
  `signal_id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '信号ID',
  `stock_code` VARCHAR(16) NOT NULL COMMENT '股票代码，如 600519.SH',
  `signal_type` ENUM('TARGET', 'ORDER') NOT NULL COMMENT 'TARGET=目标仓位, ORDER=直接下单',
  `action` ENUM('BUY', 'SELL') NULL COMMENT 'ORDER模式下必填，TARGET可空',
  `volume` INT NOT NULL COMMENT 'TARGET=目标总股数，ORDER=交易股数',
  `price_type` ENUM('MARKET', 'LIMIT') NOT NULL DEFAULT 'MARKET' COMMENT '市价/限价',
  `limit_price` DECIMAL(10,3) NULL COMMENT '限价委托价格，MARKET可为空',
  `status` ENUM('PENDING', 'PROCESSING', 'SUCCESS', 'FAILED', 'PARTIAL') NOT NULL DEFAULT 'PENDING' COMMENT '执行状态',
  `retry_count` INT NOT NULL DEFAULT 0 COMMENT '重试次数',
  `last_order_id` VARCHAR(64) NULL COMMENT '最近一次委托单号',
  `last_error` VARCHAR(500) NULL COMMENT '最近一次错误信息',
  `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`signal_id`),
  KEY `idx_status_ctime` (`status`, `create_time`),
  KEY `idx_stock_status` (`stock_code`, `status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='交易执行信号表';
