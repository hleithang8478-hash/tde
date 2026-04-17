# -*- coding: utf-8 -*-
"""
RPA 主控适配器：单线程 Worker + Queue 串行化所有 UI 操作，
与 TradeExecutor 期望的 broker 接口一致（place_order / query_order / cancel_order / get_position_volume）。

设计要点（生产）：
- 任意时刻仅 Worker 线程操作键盘鼠标；状态监控线程只通过 ``_submit`` 投递任务，避免竞态。
- 默认 ``order_ui_mouse_flow``：置前 → 可选最大化 → 鼠标依次填单 → 买入/卖出确认 → 二次确认 → 可选「递交成功后再确定」；``rpa_submit_sync_after_fill=False`` 时不扫委托表。
- 委托类型：可配两个独立控件（``mf_entrust_type_limit_xy`` / ``mf_entrust_type_market_xy``），或配下拉框（``mf_entrust_type_dropdown_xy`` + 展开后的限价/市价选项坐标）。
- 撤单等其它路径仍可用，与本轮「仅递交」主流程独立。
"""

from __future__ import annotations

import queue
import threading
import time
import traceback
import uuid
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from src.rpa.exceptions import CriticalRpaError
from src.rpa.rpa_input_engine import RpaInputEngine
from src.rpa import table_state
from src.rpa import template_registry
from src.rpa.rpa_config_manager import RpaConfigManager
from src.rpa.vision_inspector import AiVisionInspector, _ocr_best_for_trade_panel
from src.rpa.window_controller import WindowController


def _cfg() -> dict[str, Any]:
    return RpaConfigManager.load_merged()


def _error_shots_dir() -> Path:
    p = Path(__file__).resolve().parents[2] / "logs" / "error_shots"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _save_fullscreen_png() -> str:
    try:
        from PIL import ImageGrab  # type: ignore
    except Exception:
        return ""
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = _error_shots_dir() / f"err_{ts}_{uuid.uuid4().hex[:8]}.png"
    try:
        ImageGrab.grab(all_screens=True).save(str(path), format="PNG")
        return str(path)
    except Exception:
        return ""


def _dingtalk_alert(title: str, body: str, shot_path: str = "") -> None:
    url = (_cfg().get("dingtalk_webhook_url") or "").strip()
    if not url:
        print(f"[RPA][NO-WEBHOOK] {title}\n{body}\n{shot_path}")
        return
    try:
        from src.notifier.dingtalk_notifier import send_dingtalk

        extra = f"\n截图: {shot_path}" if shot_path else ""
        send_dingtalk(url, f"{title}\n{body}{extra}")
    except Exception:
        print("[RPA] 钉钉推送失败:")
        print(traceback.format_exc())


class RpaTradeAdapter:
    """单例队列 + Worker；所有 broker 方法均投递到 Worker 执行。"""

    def __init__(self) -> None:
        self._cfg = _cfg()
        self._wc = WindowController(self._cfg)
        self._vi = AiVisionInspector(self._cfg)
        self._inp = RpaInputEngine(self._cfg)
        self._q: queue.Queue = queue.Queue()
        self._orders: Dict[str, Dict[str, Any]] = {}
        self._orders_lock = threading.Lock()
        self._worker = threading.Thread(target=self._worker_loop, name="RPA-Worker", daemon=True)
        self._worker.start()

        self._stop_monitor = threading.Event()
        self._table_sync_warned = False
        self._monitor_fail_streak = 0
        if bool(self._cfg.get("status_monitor_enabled", False)):
            threading.Thread(
                target=self._status_monitor_loop,
                name="RPA-StatusMonitor",
                daemon=True,
            ).start()

    def _refresh_components_cfg(self) -> None:
        """从 src.config 重新拉取 RPA_CONFIG，避免进程长驻时子模块持有旧字典。"""
        self._cfg = _cfg()
        self._wc._cfg = self._cfg
        self._vi._cfg = self._cfg
        self._inp._cfg = self._cfg

    # ------------------------------------------------------------------
    def _worker_loop(self) -> None:
        while True:
            fn, args, kw, fut = self._q.get()
            try:
                res = fn(*args, **kw)
                fut.set_result(res)
            except BaseException as e:  # noqa: BLE001
                fut.set_exception(e)
            finally:
                self._q.task_done()

    def _submit(self, fn: Callable[..., Any], *args: Any, **kw: Any) -> Any:
        fut: Future[Any] = Future()
        self._q.put((fn, args, kw, fut))
        return fut.result(timeout=float(_cfg().get("rpa_task_timeout_sec", 600)))

    # ------------------------------------------------------------------
    def _status_monitor_loop(self) -> None:
        """仅排队同步任务，不直接碰 UI。"""
        while not self._stop_monitor.wait(float(_cfg().get("status_poll_interval_sec", 10.0))):
            try:
                self._submit(self._monitor_tick)
                self._monitor_fail_streak = 0
            except Exception as e:
                self._monitor_fail_streak += 1
                if self._monitor_fail_streak in (1, 5, 20):
                    _dingtalk_alert(
                        "[RPA] 状态监控异常",
                        f"{type(e).__name__}: {e}\n{traceback.format_exc()[:800]}",
                        _save_fullscreen_png(),
                    )

    def _monitor_tick(self) -> None:
        self._sync_orders_ui_to_cache(refresh_hotkey=True)

    # ------------------------------------------------------------------
    def _vlm_ready(self) -> bool:
        c = _cfg()
        return bool((c.get("vlm_api_url") or "").strip() and (c.get("vlm_api_key") or "").strip())

    def _sync_orders_ui_to_cache(self, *, refresh_hotkey: bool) -> None:
        """从委托表截图 + VLM 更新 ``_orders`` 中各单状态。"""
        self._refresh_components_cfg()
        cfg = self._cfg
        bbox = cfg.get("bbox_order_table")
        if not bbox or not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            if not self._table_sync_warned:
                self._table_sync_warned = True
                _dingtalk_alert(
                    "[RPA] 配置提醒",
                    "未配置 bbox_order_table [left,top,w,h]，无法从客户端同步委托状态；"
                    "query_order 将长期停留在 PENDING（除非手工改库）。",
                    "",
                )
            return
        if not self._vlm_ready():
            return

        if refresh_hotkey:
            if cfg.get("status_monitor_bring_front", True):
                self._wc.bring_to_front()
            self._wc.press_orders_hotkey()
            time.sleep(float(cfg.get("orders_table_settle_sec", 0.55)))

        b64 = self._vi.capture_region(tuple(int(x) for x in bbox))  # type: ignore[arg-type]
        rows_raw = self._vi.parse_delegate_table(b64)
        rows = table_state.normalize_vlm_rows(rows_raw)

        with self._orders_lock:
            for oid, rec in list(self._orders.items()):
                if str(rec.get("status")) == "REJECTED":
                    continue
                vol = int(rec.get("orig_volume") or rec.get("unfilled_volume") or 0)
                row = table_state.pick_row_for_our_order(
                    rows,
                    code=str(rec.get("code") or ""),
                    action=str(rec.get("action") or ""),
                    volume=vol,
                    broker_entrust_id=str(rec.get("broker_entrust_id") or ""),
                )
                if not row:
                    continue
                eid = str(row.get("entrust_id") or "")
                if eid:
                    rec["broker_entrust_id"] = eid
                rec["raw_status_cn"] = row.get("raw_status_cn", "")
                st = str(row.get("status") or "PENDING")
                fv = int(row.get("filled_volume") or 0)
                uv = int(row.get("unfilled_volume") or 0)
                ov = int(row.get("order_volume") or 0)
                rec["status"] = st
                if st == "FILLED":
                    rec["filled_volume"] = fv or ov or vol
                    rec["unfilled_volume"] = 0
                elif st == "PARTIAL":
                    rec["filled_volume"] = fv
                    rec["unfilled_volume"] = uv if uv > 0 else max(0, ov - fv)
                elif st in ("CANCELLED", "REJECTED"):
                    rec["filled_volume"] = fv
                    rec["unfilled_volume"] = uv if uv > 0 else max(0, vol - fv)
                else:
                    rec["filled_volume"] = fv
                    rec["unfilled_volume"] = uv if uv > 0 else max(0, vol - fv)

    def _post_submit_table_poll(self, oid: str) -> None:
        """下单后短窗口内轮询委托表，尽快挂上柜台委托号与状态。"""
        self._refresh_components_cfg()
        attempts = int(self._cfg.get("post_submit_sync_attempts", 4))
        gap = float(self._cfg.get("post_submit_sync_interval_sec", 1.8))
        for _ in range(max(1, attempts)):
            time.sleep(gap)
            try:
                self._sync_orders_ui_to_cache(refresh_hotkey=True)
            except Exception:
                continue
            with self._orders_lock:
                rec = self._orders.get(oid)
            if not rec:
                return
            if rec.get("broker_entrust_id") or str(rec.get("status")) != "PENDING":
                return

    def _normalize_code(self, stock_code: str) -> str:
        code = (stock_code or "").strip().upper()
        if code.endswith(".SH"):
            return code[:-3]
        if code.endswith((".SZ", ".SS")):
            return code.split(".")[0]
        return "".join(c for c in code if c.isdigit())[:6]

    def _resolve_price_for_ui(self, price_type: str, limit_price: Optional[float]) -> str:
        pt = (price_type or "MARKET").upper()
        if pt == "MARKET":
            return "market"
        if limit_price is None:
            raise ValueError("LIMIT 必须提供 limit_price")
        return f"{float(limit_price):.4f}".rstrip("0").rstrip(".")

    def _click_xy(
        self,
        xy: Any,
        pause: float,
        *,
        clicks: int = 1,
        between_click: float = 0.08,
    ) -> None:
        import pyautogui  # type: ignore

        if not xy or len(xy) != 2:
            raise CriticalRpaError("坐标无效")
        x, y = int(xy[0]), int(xy[1])
        n = max(1, int(clicks))
        if n <= 1:
            pyautogui.click(x, y)
        else:
            pyautogui.click(x, y, clicks=n, interval=max(0.0, float(between_click)))
        time.sleep(pause)

    def _mf_pause(self) -> float:
        return float(self._cfg.get("mf_click_pause_sec", 0.12))

    def _mf_need_xy(self, key: str) -> Tuple[int, int]:
        xy = self._cfg.get(key)
        if xy is None or not isinstance(xy, (list, tuple)) or len(xy) < 2:
            raise CriticalRpaError(f"已启用 order_ui_mouse_flow 但未配置或格式错误: {key}（须为 [x,y] 整屏坐标）")
        return (int(xy[0]), int(xy[1]))

    def _mf_select_entrust_type(self, price_type: str, pause: float) -> None:
        """
        限价/市价：二选一交互方式——
        A) 下拉框：先点 ``mf_entrust_type_dropdown_xy``，等待展开，再点对应 ``mf_entrust_type_*_option_xy``；
        B) 两个独立控件：点 ``mf_entrust_type_limit_xy`` 或 ``mf_entrust_type_market_xy``（与旧版一致）。
        """
        pt = str(price_type or "MARKET").upper()
        dd = self._cfg.get("mf_entrust_type_dropdown_xy")
        has_dd = dd is not None and isinstance(dd, (list, tuple)) and len(dd) >= 2

        if has_dd:
            self._click_xy((int(dd[0]), int(dd[1])), pause)
            open_wait = float(self._cfg.get("mf_entrust_type_dropdown_open_wait_sec", 0.25))
            time.sleep(open_wait)
            if pt == "LIMIT":
                opt = self._cfg.get("mf_entrust_type_limit_option_xy")
                key = "mf_entrust_type_limit_option_xy"
            else:
                opt = self._cfg.get("mf_entrust_type_market_option_xy")
                key = "mf_entrust_type_market_option_xy"
            if opt is None or not isinstance(opt, (list, tuple)) or len(opt) < 2:
                raise CriticalRpaError(
                    f"已配置 mf_entrust_type_dropdown_xy 但未配置或格式错误: {key}（下拉展开后点「限价」或「市价」那一项的中心）"
                )
            self._click_xy((int(opt[0]), int(opt[1])), pause)
            return

        if pt == "LIMIT":
            self._click_xy(self._mf_need_xy("mf_entrust_type_limit_xy"), pause)
        else:
            self._click_xy(self._mf_need_xy("mf_entrust_type_market_xy"), pause)

    def _mf_select_quantity_type(self, quantity_mode: str, pause: float) -> None:
        """
        数量类型：固定数量 / 固定金额 / 持仓比例 等——
        A) 下拉框：点 ``mf_quantity_type_dropdown_xy``，展开后按 ``quantity_mode`` 点对应 ``mf_quantity_type_*_option_xy``；
        B) 旧版：直接点 ``mf_quantity_type_shares_xy`` 或 ``mf_quantity_type_ratio_xy``。
        信号层目前仅 ABSOLUTE（固定数量）与 TARGET_PCT（持仓比例）；固定金额选项坐标可预配，待业务接入 FIXED_AMOUNT 后再用。
        """
        qm = str(quantity_mode or "ABSOLUTE").upper()
        cfg = self._cfg
        dd = cfg.get("mf_quantity_type_dropdown_xy")
        has_dd = dd is not None and isinstance(dd, (list, tuple)) and len(dd) >= 2

        if has_dd:
            self._click_xy((int(dd[0]), int(dd[1])), pause)
            open_wait = float(cfg.get("mf_quantity_type_dropdown_open_wait_sec", 0.25))
            time.sleep(open_wait)
            if qm == "TARGET_PCT":
                opt = cfg.get("mf_quantity_type_position_ratio_option_xy")
                key = "mf_quantity_type_position_ratio_option_xy"
            elif qm == "FIXED_AMOUNT":
                opt = cfg.get("mf_quantity_type_fixed_amount_option_xy")
                key = "mf_quantity_type_fixed_amount_option_xy"
            else:
                opt = cfg.get("mf_quantity_type_fixed_qty_option_xy")
                key = "mf_quantity_type_fixed_qty_option_xy"
            if opt is None or not isinstance(opt, (list, tuple)) or len(opt) < 2:
                raise CriticalRpaError(
                    f"已配置 mf_quantity_type_dropdown_xy 但未配置或格式错误: {key}（展开后点「固定数量/固定金额/持仓比例」中对应一项的中心）"
                )
            self._click_xy((int(opt[0]), int(opt[1])), pause)
            return

        ratio_xy = cfg.get("mf_quantity_type_ratio_xy")
        shares_xy = cfg.get("mf_quantity_type_shares_xy")
        has_r = ratio_xy is not None and isinstance(ratio_xy, (list, tuple)) and len(ratio_xy) >= 2
        has_s = shares_xy is not None and isinstance(shares_xy, (list, tuple)) and len(shares_xy) >= 2

        if qm == "TARGET_PCT":
            if not has_r:
                raise CriticalRpaError(
                    "quantity_mode=TARGET_PCT 时需配置 mf_quantity_type_ratio_xy，或改用 mf_quantity_type_dropdown_xy + mf_quantity_type_position_ratio_option_xy"
                )
            self._click_xy((int(ratio_xy[0]), int(ratio_xy[1])), pause)
        else:
            if not has_s:
                raise CriticalRpaError(
                    "quantity_mode=ABSOLUTE 时需配置 mf_quantity_type_shares_xy，或改用 mf_quantity_type_dropdown_xy + mf_quantity_type_fixed_qty_option_xy"
                )
            self._click_xy((int(shares_xy[0]), int(shares_xy[1])), pause)

    def _execute_order_mouse_flow(
        self,
        *,
        code: str,
        action: str,
        price_type: str,
        price_str: str,
        qty_display: str,
        quantity_mode: str = "ABSOLUTE",
    ) -> None:
        """
        固定顺序（整屏坐标）：代码框 → 买卖方向 → 限价/市价（独立按钮或下拉+选项）→ （限价则价格框）→ 数量类型（独立按钮或下拉+选项）→ 数量框。
        quantity_mode=TARGET_PCT 填百分比；ABSOLUTE 填股数；下拉 UI 时由 ``mf_quantity_type_*_option_xy`` 对应固定数量/持仓比例等。
        不在此函数内点最终买入/卖出，由后续 _click_confirm_if_configured 处理。
        """
        p = self._mf_pause()
        pt = str(price_type or "MARKET").upper()
        qm = str(quantity_mode or "ABSOLUTE").upper()

        self._click_xy(self._mf_need_xy("mf_stock_code_xy"), p)
        self._inp.type_stock_code_then_wait(code)

        dir_clicks = max(1, int(self._cfg.get("mf_direction_click_count", 1)))
        dir_gap = float(self._cfg.get("mf_direction_between_click_sec", 0.08))
        if str(action).upper() == "BUY":
            self._click_xy(
                self._mf_need_xy("mf_direction_buy_xy"),
                p,
                clicks=dir_clicks,
                between_click=dir_gap,
            )
        else:
            self._click_xy(
                self._mf_need_xy("mf_direction_sell_xy"),
                p,
                clicks=dir_clicks,
                between_click=dir_gap,
            )

        self._mf_select_entrust_type(pt, p)

        skip_price = pt == "MARKET" and bool(self._cfg.get("mf_skip_price_when_market", True))
        if not skip_price:
            self._click_xy(self._mf_need_xy("mf_price_xy"), p)
            if price_str != "market":
                self._inp.safe_type(price_str)

        self._mf_select_quantity_type(qm, p)

        self._click_xy(self._mf_need_xy("mf_quantity_xy"), p)
        self._inp.safe_type(qty_display)

    def _withdraw_template_name(self) -> str:
        return str(self._cfg.get("opencv_withdraw_template") or "withdraw_btn").strip() or "withdraw_btn"

    def _confirm_template_name(self) -> str:
        return str(self._cfg.get("opencv_confirm_template") or "buy_confirm_btn").strip() or "buy_confirm_btn"

    def _secondary_template_name(self) -> str:
        return (
            str(self._cfg.get("opencv_secondary_confirm_template") or "secondary_confirm_btn").strip()
            or "secondary_confirm_btn"
        )

    def _resolve_xy_with_opencv(
        self,
        *,
        template_name: str,
        dead_xy: Optional[Tuple[int, int]],
    ) -> Optional[Tuple[int, int]]:
        """主窗截图 + 模板匹配；失败返回 None。"""
        cap = self._wc.capture_window_bgr()
        if not cap:
            return None
        bgr, off = cap
        return self._vi.get_element_pos(
            template_name,
            scene_bgr=bgr,
            offset_xy=off,
            dead_xy=dead_xy,
        )

    def _click_confirm_if_configured(self, action: str) -> None:
        is_sell = str(action).upper() == "SELL"
        dead = self._cfg.get("confirm_sell_button_xy") if is_sell else self._cfg.get("confirm_button_xy")
        if (not dead or len(dead) < 2) and is_sell:
            dead = self._cfg.get("confirm_button_xy")
        dead_t: Optional[Tuple[int, int]] = None
        if dead and len(dead) >= 2:
            dead_t = (int(dead[0]), int(dead[1]))
        tpl_name = self._confirm_template_name()
        if is_sell:
            alt = str(self._cfg.get("opencv_confirm_sell_template") or "").strip()
            if alt and template_registry.template_png_path(alt).is_file():
                tpl_name = alt
        pos = self._resolve_xy_with_opencv(
            template_name=tpl_name,
            dead_xy=dead_t,
        )
        if pos is None and dead_t is None:
            raise CriticalRpaError(
                "未配置买入/卖出确认坐标或 OpenCV 模板，禁止盲点。"
                f"（卖出可配 confirm_sell_button_xy / opencv_confirm_sell_template）"
            )
        xy = pos if pos is not None else dead_t
        assert xy is not None
        self._click_xy(xy, float(self._cfg.get("after_confirm_wait_sec", 0.6)))

    def _click_secondary_confirm_if_configured(self) -> None:
        """柜台二次确认（如「确定下单」）；优先 OpenCV 模板 ``secondary_confirm_btn``。"""
        dead = self._cfg.get("secondary_confirm_xy")
        dead_t: Optional[Tuple[int, int]] = None
        if dead and len(dead) >= 2:
            dead_t = (int(dead[0]), int(dead[1]))
        pos = self._resolve_xy_with_opencv(
            template_name=self._secondary_template_name(),
            dead_xy=dead_t,
        )
        if pos is None and dead_t is None:
            return
        xy = pos if pos is not None else dead_t
        if xy is None:
            return
        self._click_xy(xy, float(self._cfg.get("after_secondary_confirm_wait_sec", 0.35)))

    def _click_post_order_success_ok_if_configured(self) -> None:
        """部分柜台在委托已受理后仍弹「确定」；配 ``post_order_success_ok_xy`` 或 OpenCV 模板后在此点击。"""
        dead = self._cfg.get("post_order_success_ok_xy")
        dead_t: Optional[Tuple[int, int]] = None
        if dead and len(dead) >= 2:
            dead_t = (int(dead[0]), int(dead[1]))
        tpl_raw = str(self._cfg.get("opencv_post_success_ok_template") or "").strip()
        if not tpl_raw and dead_t is None:
            return
        pos = None
        if tpl_raw:
            pos = self._resolve_xy_with_opencv(template_name=tpl_raw, dead_xy=dead_t)
        if pos is None and dead_t is None:
            return
        xy = pos if pos is not None else dead_t
        if xy is None:
            return
        time.sleep(float(self._cfg.get("before_post_order_success_ok_sec", 0.2)))
        self._click_xy(xy, float(self._cfg.get("after_post_order_success_ok_wait_sec", 0.35)))

    def _pipeline_place_order(
        self,
        stock_code: str,
        action: str,
        volume: int,
        price_type: str,
        limit_price: Optional[float] = None,
        quantity_mode: str = "ABSOLUTE",
        weight_pct: Optional[float] = None,
    ) -> str:
        shot = ""
        oid = f"rpa-{uuid.uuid4().hex}"
        self._refresh_components_cfg()
        try:
            qm0 = str(quantity_mode or "ABSOLUTE").upper()
            if qm0 == "TARGET_PCT" and not self._cfg.get("order_ui_mouse_flow", False):
                raise CriticalRpaError("quantity_mode=TARGET_PCT 时必须启用 order_ui_mouse_flow 并配置 mf_* 坐标")

            if self._cfg.get("ai_verify_enabled", True) and not self._cfg.get("bbox_pre_trade"):
                raise CriticalRpaError(
                    "已启用 AI 校验但未配置 bbox_pre_trade（下单区 left,top,w,h），拒绝盲操作。"
                )

            self._wc.bring_to_front()

            wmax = self._cfg.get("window_maximize_button_xy")
            if wmax is not None and isinstance(wmax, (list, tuple)) and len(wmax) >= 2:
                self._click_xy((int(wmax[0]), int(wmax[1])), float(self._cfg.get("after_maximize_wait_sec", 0.3)))

            code = self._normalize_code(stock_code)
            price_str = self._resolve_price_for_ui(price_type, limit_price)
            qm = str(quantity_mode or "ABSOLUTE").upper()
            if qm == "TARGET_PCT" and weight_pct is not None:
                w = float(weight_pct)
                qty_display = str(int(w)) if w == int(w) else f"{w:.4f}".rstrip("0").rstrip(".")
            else:
                qty_display = str(max(int(volume), 0))
            direction = "buy" if action.upper() == "BUY" else "sell"
            vol_for_rec = int(volume) if qm == "ABSOLUTE" else 0

            if self._cfg.get("order_ui_mouse_flow", False):
                self._execute_order_mouse_flow(
                    code=code,
                    action=action,
                    price_type=price_type,
                    price_str=price_str,
                    qty_display=qty_display,
                    quantity_mode=qm,
                )
            else:
                self._wc.global_reset(action)
                self._inp.type_stock_code_then_wait(code)
                for _ in range(int(self._cfg.get("tabs_after_code", 0))):
                    self._inp.tab_next_field()

                if price_str != "market" or not self._cfg.get("market_skip_price_field", True):
                    self._inp.safe_type(price_str)
                    self._inp.tab_next_field()
                else:
                    skips = int(self._cfg.get("tabs_market_skip_price", 1))
                    for _ in range(max(1, skips)):
                        self._inp.tab_next_field()
                self._inp.safe_type(qty_display)

            bbox = self._cfg.get("bbox_pre_trade")
            if bbox is not None and self._cfg.get("ai_verify_enabled", True):
                b64 = self._vi.capture_region(tuple(int(x) for x in bbox))  # type: ignore[arg-type]
                self._vi.verify_pre_trade_form(
                    b64,
                    {
                        "code": code,
                        "direction": direction,
                        "price": price_str,
                        "volume": qty_display,
                    },
                )

            try:
                pop = self._vi.analyze_popup(self._vi.capture_fullscreen_b64())
                if pop.get("is_blocking"):
                    shot = _save_fullscreen_png()
                    raise CriticalRpaError(f"弹窗阻断: {pop.get('content')}", shot_path=shot)
            except CriticalRpaError:
                raise
            except Exception:
                if self._cfg.get("strict_popup_ai", False):
                    raise

            with self._orders_lock:
                self._orders[oid] = {
                    "status": "PENDING",
                    "filled_volume": 0,
                    "unfilled_volume": max(vol_for_rec, 1) if qm == "ABSOLUTE" else 0,
                    "orig_volume": vol_for_rec,
                    "code": code,
                    "action": action.upper(),
                    "placed_monotonic": time.monotonic(),
                    "broker_entrust_id": "",
                    "raw_status_cn": "",
                    "quantity_mode": qm,
                    "weight_pct": weight_pct,
                }

            self._click_confirm_if_configured(action)
            self._click_secondary_confirm_if_configured()
            self._click_post_order_success_ok_if_configured()

            if self._cfg.get("rpa_submit_sync_after_fill", False):
                self._post_submit_table_poll(oid)

            with self._orders_lock:
                rec = self._orders.get(oid)
                if rec and str(rec.get("status")) == "PENDING" and not rec.get("broker_entrust_id"):
                    # 仍未在柜台表中出现：保持 PENDING，由监控线程继续拉
                    pass

            return oid
        except Exception as e:
            shot = shot or _save_fullscreen_png()
            _dingtalk_alert(
                "[RPA] 下单失败",
                f"{type(e).__name__}: {e}\n{traceback.format_exc()[:1200]}",
                shot,
            )
            if isinstance(e, CriticalRpaError):
                e.shot_path = e.shot_path or shot
            with self._orders_lock:
                if oid in self._orders:
                    self._orders[oid]["status"] = "REJECTED"
            raise
        finally:
            try:
                mouse_flow = bool(self._cfg.get("order_ui_mouse_flow", False))
                suppress_esc = bool(self._cfg.get("order_mouse_flow_suppress_post_escape", True))
                if not (mouse_flow and suppress_esc):
                    import pyautogui  # type: ignore

                    pyautogui.press("escape")
            except Exception:
                pass

    def _pipeline_cancel_order(self, order_id: str) -> bool:
        """在委托表中定位可撤行 → 撤单按钮 → 二次确认。"""
        self._refresh_components_cfg()
        shot = ""
        try:
            with self._orders_lock:
                rec = self._orders.get(str(order_id))
            if not rec:
                return True

            bbox = self._cfg.get("bbox_order_table")
            wxy = self._cfg.get("withdraw_button_xy")
            w_tpl = template_registry.template_png_path(self._withdraw_template_name()).is_file()
            if not bbox or len(bbox) != 4:
                _dingtalk_alert(
                    "[RPA] 撤单失败",
                    "未配置 bbox_order_table，无法安全撤单。",
                    shot or _save_fullscreen_png(),
                )
                return False
            if not w_tpl and (not wxy or len(wxy) != 2):
                _dingtalk_alert(
                    "[RPA] 撤单失败",
                    "未配置 withdraw_btn 模板且未配置 withdraw_button_xy，无法撤单。",
                    shot or _save_fullscreen_png(),
                )
                return False
            if not self._vlm_ready():
                _dingtalk_alert("[RPA] 撤单失败", "未配置 VLM，无法定位委托行。", shot or _save_fullscreen_png())
                return False

            self._wc.bring_to_front()
            self._wc.press_orders_hotkey()
            time.sleep(float(self._cfg.get("orders_table_settle_sec", 0.55)))

            b64 = self._vi.capture_region(tuple(int(x) for x in bbox))
            rows = table_state.normalize_vlm_rows(self._vi.parse_delegate_table(b64))
            vol = int(rec.get("orig_volume") or rec.get("unfilled_volume") or 0)
            row = table_state.pick_row_for_our_order(
                rows,
                code=str(rec.get("code") or ""),
                action=str(rec.get("action") or ""),
                volume=vol,
                broker_entrust_id=str(rec.get("broker_entrust_id") or ""),
            )
            if not row:
                return True
            if not table_state.is_cancellable_status_cn(row.get("raw_status_cn", "")):
                return True

            rel = self._vi.locate_cancel_row_center_relative(
                b64,
                code=str(rec.get("code") or ""),
                direction="buy" if str(rec.get("action")).upper() == "BUY" else "sell",
                volume=vol,
                entrust_id_hint=str(rec.get("broker_entrust_id") or row.get("entrust_id") or ""),
            )
            import pyautogui  # type: ignore

            tbl = tuple(int(x) for x in bbox)
            row_y_rel: Optional[int] = None

            if rel is not None:
                ax = int(bbox[0]) + int(rel[0])
                ay = int(bbox[1]) + int(rel[1])
                pyautogui.click(ax, ay)
                time.sleep(0.25)
                row_y_rel = int(rel[1])
            else:
                geom = self._cfg.get("order_table_row_geometry")
                if isinstance(geom, dict):
                    fx, fy = int(geom["first_row_center_xy"][0]), int(geom["first_row_center_xy"][1])
                    rh = int(geom.get("row_height", 22))
                    try:
                        idx = rows.index(row)
                    except ValueError:
                        idx = 0
                    pyautogui.click(fx, fy + idx * rh)
                    time.sleep(0.25)
                    row_y_rel = int(fy + idx * rh - bbox[1])
                else:
                    raise CriticalRpaError(
                        "VLM 未返回行坐标且未配置 order_table_row_geometry，无法选中委托行。"
                    )

            dead_w: Optional[Tuple[int, int]] = None
            if wxy and len(wxy) >= 2:
                dead_w = (int(wxy[0]), int(wxy[1]))

            if w_tpl:
                if row_y_rel is None:
                    _dingtalk_alert(
                        "[RPA] 撤单中止",
                        "无法得到行相对纵坐标，OpenCV 无法在行内定位撤单图标。",
                        shot or _save_fullscreen_png(),
                    )
                    return False
                wpos = self._vi.find_withdraw_button_on_order_row(
                    table_bbox=tbl,
                    row_center_y_rel_table=row_y_rel,
                    template_name=self._withdraw_template_name(),
                    dead_fallback_xy=dead_w,
                )
                if not wpos:
                    _dingtalk_alert(
                        "[RPA] 撤单中止",
                        f"该行水平条带内未匹配到模板 {self._withdraw_template_name()}.png（可撤图标可能灰显或不在视野），未点击撤单。",
                        _save_fullscreen_png(),
                    )
                    return False
                self._click_xy(wpos, 0.35)
            else:
                assert dead_w is not None
                self._click_xy(dead_w, 0.35)

            self._click_secondary_confirm_if_configured()
            time.sleep(0.5)
            self._sync_orders_ui_to_cache(refresh_hotkey=True)
            return True
        except Exception as e:
            shot = _save_fullscreen_png()
            _dingtalk_alert(
                "[RPA] 撤单异常",
                f"order_id={order_id} {type(e).__name__}: {e}\n{traceback.format_exc()[:1000]}",
                shot,
            )
            return False

    def _pipeline_get_position(self, stock_code: str) -> int:
        self._refresh_components_cfg()
        if not self._cfg.get("position_query_enabled", False):
            _dingtalk_alert(
                "[RPA] 持仓查询未启用",
                "get_position_volume 返回 0；请在 RPA_CONFIG 启用 position_query_enabled 并配置 bbox_positions_table。",
                _save_fullscreen_png(),
            )
            return 0
        bbox = self._cfg.get("bbox_positions_table")
        if not bbox or len(bbox) != 4:
            _dingtalk_alert(
                "[RPA] 持仓查询失败",
                "未配置 bbox_positions_table。",
                _save_fullscreen_png(),
            )
            return 0
        if not self._vlm_ready():
            return 0

        try:
            self._wc.bring_to_front()
            ptab = self._cfg.get("positions_tab_hotkey")
            if ptab:
                spec = WindowController._coerce_braced_hotkey(str(ptab), "{F5}")
                self._wc.send_keys(spec)
            else:
                pxy = self._cfg.get("positions_tab_xy")
                if pxy and len(pxy) == 2:
                    self._click_xy(pxy, float(self._cfg.get("after_positions_tab_wait_sec", 0.4)))
            time.sleep(float(self._cfg.get("positions_table_settle_sec", 0.5)))
            code = self._normalize_code(stock_code)
            b64 = self._vi.capture_region(tuple(int(x) for x in bbox))
            return int(self._vi.extract_position_available_volume(b64, code))
        except Exception as e:
            _dingtalk_alert(
                "[RPA] 持仓查询异常",
                f"{type(e).__name__}: {e}\n{traceback.format_exc()[:800]}",
                _save_fullscreen_png(),
            )
            return 0

    def _capture_panel_ocr_text_impl(self, panel: str) -> str:
        """Worker 内：点击底栏 Tab → 截取 panel_content_bbox → 多策略 Tesseract OCR。"""
        self._refresh_components_cfg()
        cfg = self._cfg
        tab_map = {
            "orders": cfg.get("panel_tab_orders_xy"),
            "trades": cfg.get("panel_tab_trades_xy"),
            "funds": cfg.get("panel_tab_funds_xy"),
            "positions": cfg.get("panel_tab_positions_xy"),
        }
        xy = tab_map.get(panel)
        if not xy or not isinstance(xy, (list, tuple)) or len(xy) < 2:
            raise CriticalRpaError(
                f"未配置或格式错误: 对应面板的 Tab 坐标（panel_tab_{panel}_xy，须为 [x,y]）"
            )
        bbox = cfg.get("panel_content_bbox")
        if not bbox or not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            raise CriticalRpaError("未配置 panel_content_bbox（须为 [left,top,width,height]）")

        if bool(cfg.get("panel_read_bring_front", True)):
            self._wc.bring_to_front()
        pause = float(self._cfg.get("mf_click_pause_sec", 0.12))
        self._click_xy((int(xy[0]), int(xy[1])), pause)
        time.sleep(float(cfg.get("panel_after_tab_wait_sec", 0.45)))

        b64 = self._vi.capture_region(tuple(int(x) for x in bbox))
        ocr_lang = (cfg.get("vlm_tesseract_lang") or "chi_sim+eng").strip() or "eng"
        tess_cmd = str(cfg.get("vlm_tesseract_cmd") or "").strip()
        tess_user_cfg = str(cfg.get("vlm_tesseract_config") or "").strip()
        txt, _tag, _sc = _ocr_best_for_trade_panel(
            b64,
            lang=ocr_lang,
            tess_cmd=tess_cmd,
            user_tesseract_config=tess_user_cfg,
            include_aggressive=bool(cfg.get("vlm_tesseract_preprocess", True)),
        )
        return (txt or "").strip()

    def capture_panel_ocr_text(self, panel: str) -> str:
        """供 UI_READ 信号：串行化点击 Tab + 区域截图 + OCR，返回纯文本。"""
        p = (panel or "").strip().lower()
        if p not in ("orders", "trades", "funds", "positions"):
            raise ValueError("panel 须为 orders|trades|funds|positions")
        return str(self._submit(self._capture_panel_ocr_text_impl, p))

    # ------------------------------------------------------------------
    def place_order(
        self,
        stock_code: str,
        action: str,
        volume: int,
        price_type: str,
        limit_price: Optional[float] = None,
        *,
        quantity_mode: str = "ABSOLUTE",
        weight_pct: Optional[float] = None,
    ) -> str:
        return self._submit(
            self._pipeline_place_order,
            stock_code,
            action,
            volume,
            price_type,
            limit_price,
            quantity_mode=quantity_mode,
            weight_pct=weight_pct,
        )

    def query_order(self, order_id: str) -> Dict[str, Any]:
        def _q() -> Dict[str, Any]:
            with self._orders_lock:
                row = self._orders.get(str(order_id))
            if not row:
                return {
                    "status": "REJECTED",
                    "filled_volume": 0,
                    "unfilled_volume": 0,
                }
            return {
                "status": str(row.get("status", "PENDING")),
                "filled_volume": int(row.get("filled_volume", 0)),
                "unfilled_volume": int(row.get("unfilled_volume", 0)),
            }

        return self._submit(_q)

    def cancel_order(self, order_id: str) -> bool:
        return bool(self._submit(self._pipeline_cancel_order, order_id))

    def get_position_volume(self, stock_code: str) -> int:
        return int(self._submit(self._pipeline_get_position, stock_code))
