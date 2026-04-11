# -*- coding: utf-8 -*-
"""交易执行核心状态机"""

import time
import traceback
from typing import Dict, Any

from src.config import DINGTALK_WEBHOOK, MAX_RETRY, ORDER_WAIT_TIMEOUT, ORDER_POLL_INTERVAL
from src.notifier.dingtalk_notifier import send_dingtalk


class TradeExecutor:
    def __init__(self, repo, broker):
        self.repo = repo
        self.broker = broker

    def process_signal(self, signal: Dict[str, Any]):
        """单条信号执行主流程，强异常保护"""
        signal_id = signal["signal_id"]
        stock_code = signal["stock_code"]
        signal_type = signal["signal_type"]
        action = signal["action"]
        volume = int(signal["volume"])
        price_type = signal["price_type"]
        retry_count = int(signal["retry_count"])

        try:
            send_dingtalk(
                DINGTALK_WEBHOOK,
                f"[开始执行]\n"
                f"信号ID: {signal_id}\n股票: {stock_code}\n类型: {signal_type}\n"
                f"动作: {action}\n数量: {volume}\n状态: PROCESSING"
            )

            if signal_type == "ORDER":
                if action not in ("BUY", "SELL"):
                    raise ValueError("ORDER模式下 action 必须是 BUY 或 SELL")
                real_action = action
                real_volume = volume

            elif signal_type == "TARGET":
                current_volume = self.broker.get_position_volume(stock_code)
                diff = volume - current_volume
                if diff == 0:
                    self.repo.update_signal_status(signal_id, "SUCCESS", retry_count=retry_count, last_error=None)
                    send_dingtalk(
                        DINGTALK_WEBHOOK,
                        f"[执行完成]\n信号ID: {signal_id}\n股票: {stock_code}\n"
                        f"TARGET目标仓位已满足，无需下单，状态: SUCCESS"
                    )
                    return

                real_action = "BUY" if diff > 0 else "SELL"
                real_volume = abs(diff)
            else:
                raise ValueError(f"未知 signal_type: {signal_type}")

            self._execute_with_retry(
                signal_id=signal_id,
                stock_code=stock_code,
                action=real_action,
                volume=real_volume,
                price_type=price_type,
                base_retry_count=retry_count
            )

        except Exception as e:
            err = f"{type(e).__name__}: {str(e)}"
            self.repo.update_signal_status(signal_id, "FAILED", retry_count=min(retry_count + 1, MAX_RETRY), last_error=err)
            send_dingtalk(
                DINGTALK_WEBHOOK,
                f"[执行异常]\n信号ID: {signal_id}\n股票: {stock_code}\n"
                f"状态: FAILED\n错误: {err}"
            )
            print(f"[ERROR] process_signal signal_id={signal_id} 异常:\n{traceback.format_exc()}")

    def _execute_with_retry(self, signal_id: int, stock_code: str, action: str, volume: int, price_type: str, base_retry_count: int):
        """委托执行状态机"""
        remaining = volume
        current_retry = base_retry_count

        while remaining > 0:
            if current_retry >= MAX_RETRY:
                self.repo.update_signal_status(
                    signal_id, "FAILED", retry_count=current_retry,
                    last_error=f"超过最大重试次数{MAX_RETRY}，剩余{remaining}股未成交"
                )
                send_dingtalk(
                    DINGTALK_WEBHOOK,
                    f"[执行失败]\n信号ID: {signal_id}\n股票: {stock_code}\n"
                    f"动作: {action}\n剩余: {remaining}\n状态: FAILED\n原因: 超过最大重试次数"
                )
                return

            order_id = self.broker.place_order(stock_code, action, remaining, price_type)
            self.repo.update_signal_status(signal_id, "PROCESSING", retry_count=current_retry, last_order_id=str(order_id), last_error=None)

            start = time.time()
            last_order_state = None
            while time.time() - start < ORDER_WAIT_TIMEOUT:
                order_info = self.broker.query_order(str(order_id))
                last_order_state = order_info
                st = order_info.get("status")
                filled = int(order_info.get("filled_volume", 0))
                unfilled = int(order_info.get("unfilled_volume", max(remaining - filled, 0)))

                if st == "FILLED":
                    remaining = 0
                    self.repo.update_signal_status(signal_id, "SUCCESS", retry_count=current_retry, last_error=None)
                    send_dingtalk(
                        DINGTALK_WEBHOOK,
                        f"[执行成功]\n信号ID: {signal_id}\n股票: {stock_code}\n动作: {action}\n状态: SUCCESS"
                    )
                    return

                if st in ("PARTIAL", "PENDING"):
                    time.sleep(ORDER_POLL_INTERVAL)
                    continue

                if st in ("REJECTED", "CANCELLED"):
                    break

                time.sleep(ORDER_POLL_INTERVAL)

            if last_order_state is None:
                current_retry += 1
                self.repo.update_signal_status(signal_id, "PARTIAL", retry_count=current_retry, last_error="未获取到订单状态，触发重试")
                send_dingtalk(
                    DINGTALK_WEBHOOK,
                    f"[重试触发]\n信号ID: {signal_id}\n股票: {stock_code}\n原因: 未获取到订单状态\n第{current_retry}次重试即将开始"
                )
                continue

            filled = int(last_order_state.get("filled_volume", 0))
            unfilled = int(last_order_state.get("unfilled_volume", max(remaining - filled, 0)))

            try:
                self.broker.cancel_order(str(order_id))
            except Exception:
                print(f"[WARN] cancel_order 失败, order_id={order_id}")

            if unfilled <= 0:
                remaining = 0
                self.repo.update_signal_status(signal_id, "SUCCESS", retry_count=current_retry, last_error=None)
                send_dingtalk(
                    DINGTALK_WEBHOOK,
                    f"[执行成功]\n信号ID: {signal_id}\n股票: {stock_code}\n动作: {action}\n状态: SUCCESS"
                )
                return

            remaining = unfilled
            current_retry += 1
            self.repo.update_signal_status(signal_id, "PARTIAL", retry_count=current_retry, last_error=f"部分成交后重试，剩余{remaining}股")
            send_dingtalk(
                DINGTALK_WEBHOOK,
                f"[部分成交重试]\n信号{signal_id}部分成交，已撤单，剩余{remaining}股，正在发起第{current_retry}次重试"
            )
