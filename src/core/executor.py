# -*- coding: utf-8 -*-
"""交易执行核心状态机"""

import logging
import time
import traceback
from typing import Dict, Any, Optional

from src.config import MAX_RETRY, ORDER_WAIT_TIMEOUT, ORDER_POLL_INTERVAL, EXECUTOR_SUBMIT_ONLY_MODE

_LOG = logging.getLogger(__name__)
from src.core.order_signal_validate import assert_order_elements_ready
from src.notifier.system_notifier import send_notification


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
        volume = int(signal.get("volume") or 0)
        price_type = signal["price_type"]
        limit_price = signal.get("limit_price")
        retry_count = int(signal["retry_count"])
        quantity_mode = str(signal.get("quantity_mode") or "ABSOLUTE").strip().upper()
        weight_pct = signal.get("weight_pct")
        if weight_pct is not None:
            try:
                weight_pct = float(weight_pct)
            except (TypeError, ValueError):
                weight_pct = None

        try:
            assert_order_elements_ready(
                {
                    "stock_code": stock_code,
                    "signal_type": signal_type,
                    "action": action,
                    "volume": volume,
                    "price_type": price_type,
                    "limit_price": limit_price,
                    "quantity_mode": quantity_mode,
                    "weight_pct": weight_pct,
                    "ui_panel": signal.get("ui_panel"),
                }
            )
        except ValueError as e:
            err = str(e)
            self.repo.update_signal_status(signal_id, "FAILED", retry_count=retry_count, last_error=err)
            send_notification(
                "EMS交易执行通知",
                f"[要素不齐]\n信号ID: {signal_id}\n股票: {stock_code}\n{err}",
            )
            print(f"[ERROR] signal_id={signal_id} 要素校验失败: {err}")
            return

        try:
            send_notification(
                "EMS交易执行通知",
                f"[开始执行]\n"
                f"信号ID: {signal_id}\n股票: {stock_code}\n类型: {signal_type}\n"
                f"动作: {action}\n数量模式: {quantity_mode}\n数量/股数: {volume}"
                + (f"\n目标仓位%: {weight_pct}" if weight_pct is not None else "")
                + (f"\n面板: {signal.get('ui_panel')}" if signal_type == "UI_READ" else "")
                + f"\n价格类型: {price_type}\n限价: {limit_price}\n状态: PROCESSING"
            )

            if signal_type == "UI_READ":
                if not hasattr(self.broker, "capture_panel_ocr_text"):
                    raise RuntimeError("当前 broker 未实现 capture_panel_ocr_text，无法执行 UI_READ")
                panel = str(signal.get("ui_panel") or "").strip().lower()
                _LOG.info(
                    "[UI_READ] 开始 signal_id=%s ui_panel=%s stock_code=%s → RPA 切 Tab、截图、OCR",
                    signal_id,
                    panel,
                    stock_code,
                )
                ocr_text = self.broker.capture_panel_ocr_text(panel)
                raw_len = len(ocr_text or "")
                _LOG.info(
                    "[UI_READ] RPA 返回文本 signal_id=%s chars=%s（即将写入 ui_ocr_text）",
                    signal_id,
                    raw_len,
                )
                if raw_len == 0:
                    _LOG.warning(
                        "[UI_READ] OCR 全文为空 signal_id=%s panel=%s，仍将标记 SUCCESS（监察台会看到空文本）",
                        signal_id,
                        panel,
                    )
                else:
                    prev = (ocr_text or "").replace("\r\n", "\n").replace("\n", " ")[:200]
                    _LOG.info('[UI_READ] OCR 节选 preview="%s%s"', prev, "…" if raw_len > 200 else "")
                self.repo.complete_ui_read_success(signal_id, ocr_text or "", retry_count)
                preview = (ocr_text or "")[:800]
                more = "…" if (ocr_text or "") and len(ocr_text) > 800 else ""
                send_notification(
                    "EMS交易执行通知",
                    f"[面板OCR完成]\n信号ID: {signal_id}\n面板: {panel}\n状态: SUCCESS\n"
                    f"OCR节选:\n{preview}{more}",
                )
                return

            if signal_type == "ORDER":
                if action not in ("BUY", "SELL"):
                    raise ValueError("ORDER模式下 action 必须是 BUY 或 SELL")
                real_action = action
                real_volume = volume
                qm_send = str(quantity_mode or "ABSOLUTE").strip().upper()
                wp_send = weight_pct

            elif signal_type == "TARGET":
                qm_in = str(quantity_mode or "ABSOLUTE").strip().upper()
                if qm_in == "TARGET_PCT" and weight_pct is not None:
                    if action not in ("BUY", "SELL"):
                        raise ValueError(
                            "TARGET 且为目标仓位百分比时，action 须为 BUY 或 SELL；"
                            "请在监察台 U2 填写「目标仓位%」并选择扩容/缩容以指定买卖方向。"
                        )
                    real_action = action
                    real_volume = 0
                    qm_send = "TARGET_PCT"
                    wp_send = weight_pct
                else:
                    from src.config import RPA_CONFIG

                    rpa_cfg = RPA_CONFIG if isinstance(RPA_CONFIG, dict) else {}
                    if not rpa_cfg.get("position_query_enabled", False):
                        raise ValueError(
                            "TARGET（按目标股数调仓）须能读持仓：请在 RPA_CONFIG 启用 position_query_enabled 并配置持仓区与 VLM；"
                            "若柜台用「持仓比例」填单，请下发 quantity_mode=TARGET_PCT 与 weight_pct，并指定 action。"
                        )
                    current_volume = self.broker.get_position_volume(stock_code)
                    diff = volume - current_volume
                    if diff == 0:
                        self.repo.update_signal_status(signal_id, "SUCCESS", retry_count=retry_count, last_error=None)
                        send_notification(
                            "EMS交易执行通知",
                            f"[执行完成]\n信号ID: {signal_id}\n股票: {stock_code}\n"
                            f"TARGET目标仓位已满足，无需下单，状态: SUCCESS"
                        )
                        return

                    real_action = "BUY" if diff > 0 else "SELL"
                    real_volume = abs(diff)
                    qm_send = "ABSOLUTE"
                    wp_send = None
            else:
                raise ValueError(f"未知 signal_type: {signal_type}")

            self._execute_with_retry(
                signal_id=signal_id,
                stock_code=stock_code,
                action=real_action,
                volume=real_volume,
                price_type=price_type,
                limit_price=limit_price,
                base_retry_count=retry_count,
                quantity_mode=qm_send,
                weight_pct=wp_send,
            )

        except Exception as e:
            err = f"{type(e).__name__}: {str(e)}"
            self.repo.update_signal_status(signal_id, "FAILED", retry_count=min(retry_count + 1, MAX_RETRY), last_error=err)
            send_notification(
                "EMS交易执行通知",
                f"[执行异常]\n信号ID: {signal_id}\n股票: {stock_code}\n"
                f"状态: FAILED\n错误: {err}"
            )
            print(f"[ERROR] process_signal signal_id={signal_id} 异常:\n{traceback.format_exc()}")

    def _execute_with_retry(
        self,
        signal_id: int,
        stock_code: str,
        action: str,
        volume: int,
        price_type: str,
        limit_price: Optional[float],
        base_retry_count: int,
        quantity_mode: str = "ABSOLUTE",
        weight_pct: Optional[float] = None,
    ):
        """委托执行状态机"""
        remaining = max(int(volume), 1) if str(quantity_mode).upper() == "ABSOLUTE" else 1
        current_retry = base_retry_count

        while remaining > 0:
            if current_retry >= MAX_RETRY:
                self.repo.update_signal_status(
                    signal_id, "FAILED", retry_count=current_retry,
                    last_error=f"超过最大重试次数{MAX_RETRY}，剩余{remaining}股未成交"
                )
                send_notification(
                    "EMS交易执行通知",
                    f"[执行失败]\n信号ID: {signal_id}\n股票: {stock_code}\n"
                    f"动作: {action}\n剩余: {remaining}\n状态: FAILED\n原因: 超过最大重试次数"
                )
                return

            order_id = self.broker.place_order(
                stock_code,
                action,
                remaining if str(quantity_mode).upper() == "ABSOLUTE" else 0,
                price_type,
                limit_price=limit_price,
                quantity_mode=quantity_mode,
                weight_pct=weight_pct,
            )
            self.repo.update_signal_status(signal_id, "PROCESSING", retry_count=current_retry, last_order_id=str(order_id), last_error=None)

            if EXECUTOR_SUBMIT_ONLY_MODE:
                self.repo.update_signal_status(signal_id, "SUCCESS", retry_count=current_retry, last_error=None)
                send_notification(
                    "EMS交易执行通知",
                    f"[已递交]\n信号ID: {signal_id}\n股票: {stock_code}\n动作: {action}\n"
                    f"柜台单号(本地): {order_id}\n说明: 当前为仅递交模式，未轮询成交。",
                )
                return

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
                    send_notification(
                        "EMS交易执行通知",
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
                send_notification(
                    "EMS交易执行通知",
                    f"[重试触发]\n信号ID: {signal_id}\n股票: {stock_code}\n原因: 未获取到订单状态\n第{current_retry}次重试即将开始"
                )
                continue

            filled = int(last_order_state.get("filled_volume", 0))
            unfilled = int(last_order_state.get("unfilled_volume", max(remaining - filled, 0)))

            try:
                self.broker.cancel_order(str(order_id))
            except Exception:
                # 注：A股撤单可能因处于撮合期而失败，此处仅做 Warning 记录，不要中断主流程
                print(f"[WARN] cancel_order 失败, order_id={order_id}")

            if unfilled <= 0:
                remaining = 0
                self.repo.update_signal_status(signal_id, "SUCCESS", retry_count=current_retry, last_error=None)
                send_notification(
                    "EMS交易执行通知",
                    f"[执行成功]\n信号ID: {signal_id}\n股票: {stock_code}\n动作: {action}\n状态: SUCCESS"
                )
                return

            remaining = unfilled
            current_retry += 1
            self.repo.update_signal_status(signal_id, "PARTIAL", retry_count=current_retry, last_error=f"部分成交后重试，剩余{remaining}股")
            send_notification(
                "EMS交易执行通知",
                f"[部分成交重试]\n信号{signal_id}部分成交，已撤单，剩余{remaining}股，正在发起第{current_retry}次重试"
            )
