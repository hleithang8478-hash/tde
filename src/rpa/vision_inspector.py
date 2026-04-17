# -*- coding: utf-8 -*-
"""
模块 2：大模型视觉安全员（VLM）+ 本地 OpenCV / OCR 辅助。

职责：
- 下单前截取「代码/方向/价格/数量」区域，调用 VLM 解析 JSON，与 EMS 指令逐字段比对。
- 异常弹窗全屏截图，让模型判断是否阻断交易。
- 本地 OpenCV ``matchTemplate``：固定按钮模板优先于死坐标，并与配置坐标偏差告警。

注意：
- DeepSeek 官方 ``/v1/chat/completions`` 的 ``deepseek-chat`` 等为**纯文本**，请求里不能带 ``image_url``，否则会 400。
  对 ``api.deepseek.com`` 默认改为「本机 Tesseract OCR + 纯文本 Chat」；多模态请换 OpenAI/通义等或设 ``vlm_chat_mode`` 为 ``vision``（需对方 API 真支持图）。
- 生产环境务必配置 HTTPS API Key，且仅走可信线路。
- 解析失败、JSON 不合规、与预期不一致时一律视为「阻断」，由上层抛 CriticalRpaError。
"""

from __future__ import annotations

import base64
import io
import json
import logging
import math
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse, urlunparse

import requests

from src.rpa import template_registry

logger = logging.getLogger(__name__)


def _normalize_vlm_api_url(url: str) -> str:
    """
    文档里的「API 根」与 POST 目标不同：根路径会 404。
    若未包含 chat/completions，则补全为 OpenAI 兼容的 /v1/chat/completions。
    """
    s = (url or "").strip()
    if not s:
        return s
    if "chat/completions" in s.lower():
        return s.rstrip("/")
    p = urlparse(s)
    if not p.scheme:
        s = "https://" + s
        p = urlparse(s)
    path = (p.path or "").rstrip("/")
    if path in ("", "/"):
        new_path = "/v1/chat/completions"
    elif path == "/v1":
        new_path = "/v1/chat/completions"
    else:
        return s.rstrip("/")
    return urlunparse((p.scheme, p.netloc, new_path, "", "", ""))


def _preprocess_pil_for_ocr(img: Any, mode: str = "aggressive") -> Any:
    """
    放大过小截图并增强对比。
    ``mild``：仅缩放 + autocontrast，适合抗锯齿字体；``aggressive``：二值化，部分界面会变糊。
    """
    from PIL import ImageOps  # type: ignore

    if not mode or mode == "none":
        return img
    if mode == "mild":
        try:
            from PIL import Image as PILImage  # type: ignore

            g = img.convert("L")
            w, h = g.size
            if min(w, h) < 220:
                g = g.resize((w * 2, h * 2), PILImage.Resampling.LANCZOS)
            return ImageOps.autocontrast(g)
        except Exception:
            return img

    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
        from PIL import Image as PILImage  # type: ignore

        gray = np.array(img.convert("L"))
        h, w = gray.shape[:2]
        if min(h, w) < 200:
            gray = cv2.resize(gray, (w * 2, h * 2), interpolation=cv2.INTER_CUBIC)
        gray = cv2.bilateralFilter(gray, 5, 40, 40)
        thr = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 35, 8
        )
        return PILImage.fromarray(thr)
    except Exception:
        return _preprocess_pil_for_ocr(img, mode="mild")


def _ocr_signal_strength(text: str) -> int:
    """粗评 OCR 是否像「下单区」：数字、汉字、有效长度。"""
    t = re.sub(r"\s+", "", text or "")
    if not t:
        return 0
    digits = sum(1 for c in t if c.isdigit())
    han = sum(1 for c in t if "\u4e00" <= c <= "\u9fff")
    return len(t) + digits * 5 + han * 4


def _save_bbox_png_debug(image_b64: str, stem: str = "ocr_pre_trade") -> str:
    """把 bbox 原始截图落盘，便于核对框是否偏了。"""
    out = _opencv_error_shots_dir() / f"{stem}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.png"
    try:
        out.write_bytes(base64.b64decode(image_b64, validate=False))
        return str(out)
    except Exception:
        return ""


def _ocr_png_base64_to_text(
    image_b64: str,
    *,
    lang: str,
    tesseract_cmd: str = "",
    preprocess: bool = True,
    preprocess_mode: str = "aggressive",
    tesseract_config: str = "--oem 3 --psm 6",
) -> str:
    """将 PNG Base64 用 Tesseract 抽文本，供不支持多模态的 Chat API 使用。"""
    import pytesseract  # type: ignore
    from PIL import Image  # type: ignore

    if (tesseract_cmd or "").strip():
        pytesseract.pytesseract.tesseract_cmd = (tesseract_cmd or "").strip()

    raw = base64.b64decode(image_b64 or "", validate=False)
    img = Image.open(io.BytesIO(raw))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    if not preprocess:
        proc = img
    else:
        proc = _preprocess_pil_for_ocr(img, mode=preprocess_mode or "aggressive")

    cfg = (tesseract_config or "").strip() or "--oem 3 --psm 6"
    try:
        return pytesseract.image_to_string(proc, lang=lang or "eng", config=cfg).strip()
    except pytesseract.TesseractNotFoundError as e:
        raise RuntimeError(
            "未找到 Tesseract-OCR 可执行文件。请安装并加入 PATH（Windows 常见安装包见 "
            "https://github.com/UB-Mannheim/tesseract/wiki ），安装时勾选中文（chi_sim）更利于识别交易界面。"
        ) from e
    except Exception as e:
        msg = str(e).lower()
        if (lang or "").lower() != "eng" and (
            "traineddata" in msg or "language" in msg or "chi_sim" in msg or "could not load" in msg
        ):
            logger.warning("Tesseract 语言 %r 不可用，改用 eng：%s", lang, e)
            return _ocr_png_base64_to_text(
                image_b64,
                lang="eng",
                tesseract_cmd=tesseract_cmd,
                preprocess=preprocess,
                preprocess_mode=preprocess_mode,
                tesseract_config=tesseract_config,
            )
        raise RuntimeError(f"OCR 失败: {e}") from e


def _ocr_best_for_trade_panel(
    image_b64: str,
    *,
    lang: str,
    tess_cmd: str,
    user_tesseract_config: str,
    include_aggressive: bool = True,
) -> tuple[str, str, int]:
    """
    多策略 OCR，取信号最强的一版。
    返回 (text, strategy_tag, score)。
    """
    base = (user_tesseract_config or "").strip() or "--oem 3 --psm 6"
    fallbacks = [base, "--oem 3 --psm 6", "--oem 3 --psm 11", "--oem 3 --psm 3"]
    seen: set[str] = set()
    tess_cfgs: list[str] = []
    for c in fallbacks:
        if c not in seen:
            seen.add(c)
            tess_cfgs.append(c)

    attempts: list[tuple[str, bool, str, str]] = []
    for tc in tess_cfgs:
        row = [
            (f"mild+{tc}", True, "mild", tc),
            (f"raw+{tc}", False, "none", tc),
            (f"aggr+{tc}", True, "aggressive", tc),
        ]
        if not include_aggressive:
            row = [x for x in row if x[2] != "aggressive"]
        attempts.extend(row)

    best_text, best_tag, best_sc = "", "", -1
    for tag, use_pre, pmode, tc in attempts:
        try:
            txt = _ocr_png_base64_to_text(
                image_b64,
                lang=lang,
                tesseract_cmd=tess_cmd,
                preprocess=use_pre,
                preprocess_mode=pmode,
                tesseract_config=tc,
            )
        except Exception:
            continue
        sc = _ocr_signal_strength(txt)
        if sc > best_sc:
            best_sc, best_text, best_tag = sc, txt, tag
    return best_text, best_tag, best_sc


def _opencv_error_shots_dir() -> Path:
    p = Path(__file__).resolve().parents[2] / "logs" / "error_shots"
    p.mkdir(parents=True, exist_ok=True)
    return p


class AiVisionInspector:
    """封装截图、Base64 编码与 VLM 调用。"""

    def __init__(self, cfg: dict[str, Any]):
        self._cfg = cfg or {}

    # ------------------------------------------------------------------
    # 截图
    # ------------------------------------------------------------------
    def capture_region(self, bbox: Optional[Tuple[int, int, int, int]]) -> str:
        """
        截取屏幕矩形区域，返回 PNG 的 Base64（无 data: 前缀）。

        :param bbox: (left, top, width, height)；None 表示全屏
        """
        try:
            from PIL import ImageGrab  # type: ignore
        except Exception as e:
            raise RuntimeError("需要 pillow：pip install pillow") from e

        try:
            if bbox is None:
                img = ImageGrab.grab(all_screens=True)
            else:
                left, top, w, h = bbox
                img = ImageGrab.grab(bbox=(left, top, left + w, top + h), all_screens=True)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception as e:
            raise RuntimeError(f"截图失败: {e}") from e

    def capture_fullscreen_b64(self) -> str:
        """全屏 PNG Base64，用于弹窗分析。"""
        return self.capture_region(None)

    # ------------------------------------------------------------------
    # OpenCV 模板匹配（本地兜底）
    # ------------------------------------------------------------------
    def _opencv_match_threshold(self) -> float:
        try:
            return float(self._cfg.get("opencv_match_threshold", 0.82))
        except Exception:
            return 0.82

    def _opencv_search_margin(self) -> int:
        try:
            return max(0, int(self._cfg.get("opencv_search_margin", 48)))
        except Exception:
            return 48

    def _template_meta(self, template_name: str) -> Dict[str, Any]:
        """来自 ``RPA_CONFIG['opencv_templates']`` 或 ``assets/registry.json`` 合并项。"""
        root = self._cfg.get("opencv_templates") or {}
        if not isinstance(root, dict):
            return {}
        meta = root.get(template_name)
        return meta if isinstance(meta, dict) else {}

    def save_debug_image(
        self,
        *,
        stage: str,
        template_name: str,
        scene_bgr: Any,
        search_rect_scene: Tuple[int, int, int, int],
        match_top_left_scene: Tuple[int, int],
        tpl_wh: Tuple[int, int],
        score: float,
        threshold: float,
    ) -> str:
        """
        在 ``scene_bgr`` 上绘制搜索 ROI（绿框）、最佳匹配矩形（红框）、匹配中心十字及分数文本，
        保存到 ``logs/error_shots/``。返回保存路径；失败返回空字符串。

        ``search_rect_scene`` / ``match_top_left_scene`` 均为相对 ``scene_bgr`` 左上角像素坐标。
        """
        import cv2  # type: ignore
        import numpy as np  # type: ignore

        try:
            vis = np.array(scene_bgr, dtype=np.uint8, copy=True)
            if vis.ndim != 3 or vis.shape[2] != 3:
                return ""
        except Exception:
            return ""

        sx, sy, sw, sh = (int(search_rect_scene[0]), int(search_rect_scene[1]), int(search_rect_scene[2]), int(search_rect_scene[3]))
        mx, my = int(match_top_left_scene[0]), int(match_top_left_scene[1])
        tw, th = int(tpl_wh[0]), int(tpl_wh[1])
        h, w = vis.shape[:2]
        sx = max(0, min(sx, w - 1))
        sy = max(0, min(sy, h - 1))
        sw = max(1, min(sw, w - sx))
        sh = max(1, min(sh, h - sy))
        mx = max(0, min(mx, w - 1))
        my = max(0, min(my, h - 1))

        green = (0, 200, 0)
        red = (0, 0, 255)
        magenta = (255, 0, 255)
        cv2.rectangle(vis, (sx, sy), (sx + sw - 1, sy + sh - 1), green, 2)
        cv2.rectangle(vis, (mx, my), (min(mx + tw - 1, w - 1), min(my + th - 1, h - 1)), red, 2)
        cx, cy = mx + tw // 2, my + th // 2
        cv2.drawMarker(
            vis,
            (int(cx), int(cy)),
            magenta,
            markerType=cv2.MARKER_CROSS,
            markerSize=max(16, min(tw, th) // 2),
            thickness=2,
        )

        line1 = f"{stage} tpl={template_name} score={score:.4f} thr={threshold:.4f}"
        line2 = f"search=({sx},{sy},{sw},{sh}) match_tl=({mx},{my}) tpl_wh=({tw},{th})"
        y0 = 22
        for i, line in enumerate((line1, line2)):
            y = y0 + i * 22
            cv2.putText(
                vis,
                line,
                (8, y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (0, 0, 0),
                3,
                cv2.LINE_AA,
            )
            cv2.putText(
                vis,
                line,
                (8, y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )

        ts = time.strftime("%Y%m%d_%H%M%S")
        safe_tpl = re.sub(r"[^\w\-]+", "_", str(template_name))[:40]
        fname = f"opencv_debug_{stage}_{safe_tpl}_{ts}_{uuid.uuid4().hex[:6]}.png"
        out = _opencv_error_shots_dir() / fname
        try:
            if not cv2.imwrite(str(out), vis):
                return ""
        except Exception:
            logger.exception("[OpenCV] save_debug_image 写入失败: %s", out)
            return ""
        logger.warning("[OpenCV] 匹配不足已保存调试图: %s", out)
        return str(out)

    def get_element_pos(
        self,
        template_name: str,
        *,
        scene_bgr: Any,
        offset_xy: Tuple[int, int],
        dead_xy: Optional[Tuple[int, int]] = None,
    ) -> Optional[Tuple[int, int]]:
        """
        在 ``scene_bgr``（主窗截图）上以模板 ``src/rpa/assets/{template_name}.png`` 做匹配，
        返回**屏幕绝对坐标**下的点击中心点 ``(cx, cy)``。

        ``offset_xy`` 为 ``scene_bgr`` 左上角在屏幕上的位置 ``(left, top)``。

        若存在 ``dead_xy`` 且与匹配结果欧氏距离超过 ``opencv_dead_warn_px``（默认 50），
        记 warning 日志并以 OpenCV 结果为准。
        """
        import cv2  # type: ignore
        import numpy as np  # type: ignore

        path = template_registry.template_png_path(template_name)
        if not path.is_file():
            return None
        tpl = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if tpl is None or tpl.size == 0:
            return None
        th, tw = tpl.shape[:2]
        if scene_bgr is None or getattr(scene_bgr, "size", 0) == 0:
            return None
        scene = scene_bgr
        if scene.shape[0] < th or scene.shape[1] < tw:
            return None

        meta = self._template_meta(template_name)
        margin = self._opencv_search_margin()
        win_h, win_w = scene.shape[:2]
        thr = self._opencv_match_threshold()
        rl = rt = rw = rh = 0
        if meta.get("rel_left") is not None and meta.get("width") is not None:
            rl = max(0, int(meta["rel_left"]) - margin)
            rt = max(0, int(meta["rel_top"]) - margin)
            rw = min(win_w - rl, int(meta["width"]) + 2 * margin)
            rh = min(win_h - rt, int(meta["height"]) + 2 * margin)
            sub = scene[rt : rt + max(1, rh), rl : rl + max(1, rw)]
            ox, oy = offset_xy[0] + rl, offset_xy[1] + rt
        else:
            sub = scene
            ox, oy = offset_xy
            rl, rt, rw, rh = 0, 0, win_w, win_h

        if sub.shape[0] < th or sub.shape[1] < tw:
            return None

        res = cv2.matchTemplate(sub, tpl, cv2.TM_CCOEFF_NORMED)
        _min_v, max_v, _min_loc, max_loc = cv2.minMaxLoc(res)
        if max_v < thr:
            logger.info(
                "[OpenCV] 模板 %s 匹配度不足: %.3f < %.3f",
                template_name,
                max_v,
                thr,
            )
            mxs = rl + int(max_loc[0])
            mys = rt + int(max_loc[1])
            self.save_debug_image(
                stage="get_element_pos",
                template_name=template_name,
                scene_bgr=scene,
                search_rect_scene=(rl, rt, rw, rh),
                match_top_left_scene=(mxs, mys),
                tpl_wh=(tw, th),
                score=float(max_v),
                threshold=float(thr),
            )
            return None

        cx = int(ox + max_loc[0] + tw // 2)
        cy = int(oy + max_loc[1] + th // 2)
        warn_px = 50
        try:
            warn_px = int(self._cfg.get("opencv_dead_warn_px", 50))
        except Exception:
            pass
        if dead_xy is not None:
            d = math.hypot(cx - float(dead_xy[0]), cy - float(dead_xy[1]))
            if d > float(warn_px):
                logger.warning(
                    "[OpenCV] 模板 %s 匹配中心与配置死坐标偏差 %.1fpx（阈值 %d），采用 OpenCV 坐标 (%d,%d)，"
                    "配置为 (%d,%d)，match_score=%.3f",
                    template_name,
                    d,
                    warn_px,
                    cx,
                    cy,
                    int(dead_xy[0]),
                    int(dead_xy[1]),
                    max_v,
                )
        return cx, cy

    def find_withdraw_button_on_order_row(
        self,
        *,
        table_bbox: Tuple[int, int, int, int],
        row_center_y_rel_table: int,
        template_name: str = "withdraw_btn",
        dead_fallback_xy: Optional[Tuple[int, int]] = None,
    ) -> Optional[Tuple[int, int]]:
        """
        在委托表 ``table_bbox`` 内，于 VLM 给出的行中心纵坐标（相对表图）所在水平条带内，
        用 OpenCV 搜索撤单图标模板；命中则返回**屏幕**点击中心。

        若模板文件不存在或匹配失败，返回 None（调用方应视为「该行无可点撤单」）。
        """
        import cv2  # type: ignore
        from PIL import ImageGrab  # type: ignore
        import numpy as np  # type: ignore

        path = template_registry.template_png_path(template_name)
        if not path.is_file():
            return None
        tpl = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if tpl is None or tpl.size == 0:
            return None
        th, tw = tpl.shape[:2]

        tbl_l, tbl_t, tbl_w, tbl_h = (int(table_bbox[0]), int(table_bbox[1]), int(table_bbox[2]), int(table_bbox[3]))
        half = int(self._cfg.get("opencv_withdraw_strip_half_height", 18))
        half = max(half, th // 2 + 2)
        row_y_screen = tbl_t + int(row_center_y_rel_table)
        strip_top = max(0, row_y_screen - half)
        strip_h = max(th + 4, half * 2)
        strip_top = min(strip_top, max(0, tbl_t + tbl_h - strip_h))

        try:
            pil = ImageGrab.grab(
                bbox=(tbl_l, strip_top, tbl_l + tbl_w, strip_top + strip_h),
                all_screens=True,
            )
            rgb = np.asarray(pil.convert("RGB"), dtype=np.uint8)
            strip_bgr = rgb[:, :, ::-1].copy()
        except Exception:
            return None

        if strip_bgr.shape[0] < th or strip_bgr.shape[1] < tw:
            return None

        res = cv2.matchTemplate(strip_bgr, tpl, cv2.TM_CCOEFF_NORMED)
        _min_v, max_v, _min_loc, max_loc = cv2.minMaxLoc(res)
        if max_v < self._opencv_match_threshold():
            logger.info(
                "[OpenCV] 行内撤单模板 %s 未达阈值: %.3f",
                template_name,
                max_v,
            )
            return None

        cx = int(tbl_l + max_loc[0] + tw // 2)
        cy = int(strip_top + max_loc[1] + th // 2)
        warn_px = int(self._cfg.get("opencv_dead_warn_px", 50))
        if dead_fallback_xy is not None:
            d = math.hypot(cx - float(dead_fallback_xy[0]), cy - float(dead_fallback_xy[1]))
            if d > float(warn_px):
                logger.warning(
                    "[OpenCV] 行内撤单匹配与 withdraw_button_xy 偏差 %.1fpx，采用匹配坐标 (%d,%d)",
                    d,
                    cx,
                    cy,
                )
        return cx, cy

    # ------------------------------------------------------------------
    # VLM 调用（OpenAI 兼容 Chat Completions；多模态或 OCR+文本）
    # ------------------------------------------------------------------
    def _resolve_vlm_transport(self, api_url: str) -> str:
        """
        ``vision``：multipart content（image_url），需服务端支持多模态。
        ``text``：仅字符串 content，截图先经本机 OCR。
        """
        mode = (self._cfg.get("vlm_chat_mode") or "auto").strip().lower()
        if mode in ("text", "ocr", "ocr_then_llm"):
            return "text"
        if mode == "vision":
            return "vision"
        if mode != "auto":
            logger.warning("未知 vlm_chat_mode=%r，按 vision 处理", mode)
            return "vision"
        host = urlparse(api_url).netloc.lower()
        if "deepseek.com" in host:
            return "text"
        return "vision"

    def _effective_vlm_transport(self) -> str:
        raw_url = (self._cfg.get("vlm_api_url") or "").strip()
        url = _normalize_vlm_api_url(raw_url)
        return self._resolve_vlm_transport(url)

    def _vlm_chat(
        self,
        system_prompt: str,
        user_text: str,
        image_base64: str,
        timeout: int = 60,
        *,
        text_only_body: Optional[str] = None,
    ) -> str:
        raw_url = (self._cfg.get("vlm_api_url") or "").strip()
        url = _normalize_vlm_api_url(raw_url)
        if raw_url and url != raw_url.rstrip("/"):
            logger.info("VLM API URL 已补全为 Chat 接口: %s", url)
        key = (self._cfg.get("vlm_api_key") or "").strip()
        model = (self._cfg.get("vlm_model") or "deepseek-chat").strip()
        if not url or not key:
            raise RuntimeError("未配置 vlm_api_url / vlm_api_key，无法执行视觉安全校验。")

        transport = self._resolve_vlm_transport(url)
        if transport == "text":
            logger.info(
                "VLM 使用 OCR+纯文本（本机 Tesseract）；官方 DeepSeek Chat 不接受图片字段。"
                "若走支持多模态的代理，请在 RPA_CONFIG 设 vlm_chat_mode 为 vision。"
            )
            if text_only_body is not None:
                user_body = text_only_body
            else:
                ocr_lang = (self._cfg.get("vlm_tesseract_lang") or "chi_sim+eng").strip() or "eng"
                tess_cmd = str(self._cfg.get("vlm_tesseract_cmd") or "").strip()
                tess_user_cfg = str(self._cfg.get("vlm_tesseract_config") or "").strip()
                ocr_text, _tag, _sc = _ocr_best_for_trade_panel(
                    image_base64,
                    lang=ocr_lang,
                    tess_cmd=tess_cmd,
                    user_tesseract_config=tess_user_cfg,
                    include_aggressive=bool(self._cfg.get("vlm_tesseract_preprocess", True)),
                )
                user_body = (
                    user_text
                    + "\n\n【以下为截图经本机 Tesseract OCR 抽出的文字，可能有缺字、错位；"
                    "请只据此判断并按要求输出 JSON】\n"
                    + (ocr_text or "(OCR 无输出)")
                )
            user_message: Dict[str, Any] = {"role": "user", "content": user_body}
        else:
            user_message = {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{image_base64}",
                        },
                    },
                ],
            }

        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                user_message,
            ],
            "max_tokens": 800,
            "temperature": 0,
        }
        r: Optional[requests.Response] = None
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=timeout)
            r.raise_for_status()
            data = r.json()
            return str(data["choices"][0]["message"]["content"] or "")
        except requests.exceptions.HTTPError as e:
            detail = ""
            if e.response is not None:
                try:
                    detail = (e.response.text or "")[:2000]
                except Exception:
                    detail = ""
            msg = f"VLM 请求失败: {e}"
            if detail:
                msg += f" | 响应节选: {detail}"
            raise RuntimeError(msg) from e
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
            body = (r.text[:1200] if r is not None else "")
            raise RuntimeError(f"VLM 响应格式异常: {e} | body[:1200]={body!r}") from e
        except Exception as e:
            raise RuntimeError(f"VLM 请求失败: {e}") from e

    @staticmethod
    def _extract_json_object(text: str) -> Dict[str, Any]:
        """从模型回复中抠出第一个 JSON 对象（允许 markdown 代码块）。"""
        t = text.strip()
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*```\s*$", "", t)
        m = re.search(r"\{[\s\S]*\}", t)
        if not m:
            raise ValueError("模型回复中未找到 JSON 对象")
        return json.loads(m.group(0))

    # ------------------------------------------------------------------
    # 对外：下单前校验
    # ------------------------------------------------------------------
    def verify_pre_trade_form(
        self,
        image_base64: str,
        expected_order: Dict[str, Any],
    ) -> None:
        """
        【触发 AI 截图 1】比对界面与 EMS 参数是否 100% 一致。

        expected_order 键建议：
        - code: 6 位或带后缀，统一大写比较
        - direction: "buy" / "sell"（小写）
        - price: 市价时可为 "market" 或与界面一致字符串
        - volume: 整数字符串
        """
        if not self._cfg.get("ai_verify_enabled", True):
            return

        transport = self._effective_vlm_transport()
        ocr_debug = ""

        if transport == "text":
            ocr_lang = (self._cfg.get("vlm_tesseract_lang") or "chi_sim+eng").strip() or "eng"
            tess_cmd = str(self._cfg.get("vlm_tesseract_cmd") or "").strip()
            tess_user_cfg = str(self._cfg.get("vlm_tesseract_config") or "").strip()
            ocr_debug, ocr_strategy, ocr_score = _ocr_best_for_trade_panel(
                image_base64,
                lang=ocr_lang,
                tess_cmd=tess_cmd,
                user_tesseract_config=tess_user_cfg,
                include_aggressive=bool(self._cfg.get("vlm_tesseract_preprocess", True)),
            )
            logger.info(
                "下单前 OCR 多策略结果: score=%s strategy=%s 预览=%r",
                ocr_score,
                ocr_strategy,
                (ocr_debug or "")[:120],
            )
            if not (ocr_debug or "").strip():
                shot = _save_bbox_png_debug(image_base64, "bbox_pre_trade_empty_ocr")
                raise RuntimeError(
                    "下单前校验中止：bbox_pre_trade 截取区域内 OCR 未识别到任何文字。"
                    "请放大该区域坐标、安装 Tesseract 中文语言包(chi_sim)、或设置 vlm_tesseract_cmd；"
                    f"已保存 bbox 截图: {shot or '失败'}。调试可暂时将 ai_verify_enabled 设为 False。"
                )
            min_sig = int(self._cfg.get("ai_verify_min_ocr_signal", 14))
            if ocr_score < min_sig:
                shot = _save_bbox_png_debug(image_base64, "bbox_pre_trade_weak_ocr")
                raise RuntimeError(
                    f"下单前校验中止：OCR 信号过弱(score={ocr_score} < 阈值 {min_sig})，"
                    f"多策略最佳为 {ocr_strategy!r}，节选 OCR={ocr_debug[:200]!r}。"
                    "说明 bbox_pre_trade 很可能未盖住代码/价格/数量文字，或界面为「仅图标无衬线文字」。"
                    f"已保存 bbox 截图: {shot or '失败'}。可尝试：扩大 bbox、将 vlm_tesseract_preprocess 设为 False、"
                    "vlm_tesseract_config 改为 '--oem 3 --psm 11'，或换支持多模态的 API(vlm_chat_mode=vision)，或关闭 ai_verify_enabled。"
                )
            system_prompt = (
                "你是证券下单界面的文本解析器。输入来自 OCR，不是像素图。"
                "必须根据 OCR 文本提取字段，输出仅一行 JSON，禁止 Markdown，禁止输出空对象 {}。"
                "若某字段在 OCR 中完全找不到，用 null 作为该键的值（不要用空字符串凑数）。"
            )
            user_body = (
                "从下列 OCR 文本中解析证券委托界面信息（允许乱序、噪声、缺行）。\n"
                "必须输出且仅输出一个 JSON 对象，包含四个键（小写）："
                "code（6位数字字符串）、direction（buy 或 sell）、"
                "price（限价填数字字符串，市价填小写 market）、volume（委托数量整数字符串）。\n"
                "direction：OCR 含「买入、买、B」等则 buy；含「卖出、卖、S」等则 sell。\n"
                "【OCR 文本】\n"
                f"{ocr_debug}\n"
                "【结束】"
            )
            raw = self._vlm_chat(system_prompt, "", image_base64, text_only_body=user_body)
        else:
            system_prompt = (
                "你是证券交易软件界面审计员。只根据图片内容回答，不要猜测。"
                "必须严格输出一个 JSON 对象，不要其它说明文字。"
            )
            user_prompt = (
                "图片是一个交易软件的下单界面。请提取：股票代码、买入/卖出方向、价格、数量。\n"
                "严格以 JSON 返回，键名固定为小写："
                '{"code": "600519", "direction": "buy", "price": "185.50", "volume": "100"}\n'
                "direction 只能是 buy 或 sell；code 为 6 位数字（忽略后缀）；"
                "price 若界面为市价则填 market；volume 为整数。"
            )
            raw = self._vlm_chat(system_prompt, user_prompt, image_base64)

        try:
            parsed = self._extract_json_object(raw)
        except Exception as e:
            raise RuntimeError(
                f"下单前校验：模型返回无法解析为 JSON：{e} | 原始前500字={raw[:500]!r} | "
                f"OCR前280字={(ocr_debug[:280] if ocr_debug else '')!r}"
            ) from e

        if transport == "text" and parsed == {}:
            raise RuntimeError(
                "下单前校验：模型返回空 JSON 对象。"
                f"请检查 bbox_pre_trade 是否覆盖下单区；OCR 前400字={ocr_debug[:400]!r}"
            )

        def _cell(v: Any) -> str:
            if v is None:
                return ""
            return str(v).strip()

        exp_code = _norm_code(expected_order.get("code"))
        got_code = _norm_code(parsed.get("code"))
        exp_dir = str(expected_order.get("direction", "")).lower().strip()
        got_dir = _cell(parsed.get("direction")).lower()
        exp_price = _norm_price(expected_order.get("price"))
        got_price = _norm_price(parsed.get("price"))
        exp_vol = str(expected_order.get("volume", "")).strip()
        got_vol = _cell(parsed.get("volume"))

        if transport == "text" and (not got_code or got_dir not in ("buy", "sell") or not got_vol):
            raise RuntimeError(
                "下单前校验：OCR+模型未能可靠解析 code/direction/volume。"
                f"期望 code={exp_code} dir={exp_dir} vol={exp_vol} | 得到 code={got_code} dir={got_dir} vol={got_vol} | "
                f"模型原始={raw[:500]!r} | OCR前400字={ocr_debug[:400]!r}"
            )

        if got_code != exp_code or got_dir != exp_dir or got_price != exp_price or got_vol != exp_vol:
            raise RuntimeError(
                "AI 下单前校验与指令不一致："
                f"期望 code={exp_code} dir={exp_dir} price={exp_price} vol={exp_vol} | "
                f"识别 code={got_code} dir={got_dir} price={got_price} vol={got_vol} | 原始={raw[:400]}"
                + (f" | OCR节选={ocr_debug[:220]!r}" if ocr_debug else "")
            )

    # ------------------------------------------------------------------
    # 对外：弹窗分析
    # ------------------------------------------------------------------
    def parse_delegate_table(self, image_base64: str) -> list[Dict[str, Any]]:
        """
        【触发 AI 截图 3】解析「当日委托」表格裁剪图。

        返回 rows 列表，元素字段尽量包含：
        entrust_id, status, code, side, order_volume, filled_volume, unfilled_volume
        """
        system_prompt = (
            "你是证券交易软件表格 OCR。只依据图片中的文字与数字，不要臆造。"
            "必须只输出一个 JSON 对象，键 rows 为数组。"
        )
        user_prompt = (
            "图片为「当日委托」或类似委托列表的表格区域（含表头与数据行）。\n"
            "请提取每个数据行的：委托编号 entrust_id、委托状态 status（中文原文）、"
            "股票代码 code（6 位数字）、买卖方向 side（买入 或 卖出）、"
            "委托数量 order_volume、成交数量 filled_volume；若有无用列可忽略。\n"
            "若某格看不清填合理空字符串或 0。严格输出 JSON 示例：\n"
            '{"rows":[{"entrust_id":"123","status":"已报","code":"600519",'
            '"side":"买入","order_volume":"100","filled_volume":"0","unfilled_volume":"100"}]}'
        )
        raw = self._vlm_chat(system_prompt, user_prompt, image_base64, timeout=90)
        data = self._extract_json_object(raw)
        rows = data.get("rows")
        if not isinstance(rows, list):
            return []
        out: list[Dict[str, Any]] = []
        for item in rows:
            if isinstance(item, dict):
                out.append(item)
        return out

    def locate_cancel_row_center_relative(
        self,
        image_base64: str,
        *,
        code: str,
        direction: str,
        volume: int,
        entrust_id_hint: str = "",
    ) -> Optional[Tuple[int, int]]:
        """
        在表格裁剪图内，找到「仍可撤单」且与目标委托匹配的数据行，
        返回该行中心点相对于**裁剪图左上角**的像素坐标 (rel_x, rel_y)。
        找不到则返回 None。
        """
        system_prompt = "你是 UI 几何定位助手。只输出 JSON，不要解释。"
        user_prompt = (
            f"在图片的委托表格中，找到同时满足：股票代码含 {code}、"
            f"方向为{'买入' if str(direction).lower() == 'buy' else '卖出'}、"
            f"委托数量约 {volume}、且委托状态仍可能允许撤单（非已成/已撤/废单）"
            f"{'、委托编号为 ' + entrust_id_hint if entrust_id_hint else ''}"
            "的那一行。\n"
            "返回 JSON："
            '{"found": true, "rel_x": 120, "rel_y": 340, "entrust_id": "实际编号"}；'
            "若找不到 found=false。"
        )
        raw = self._vlm_chat(system_prompt, user_prompt, image_base64, timeout=90)
        data = self._extract_json_object(raw)
        if not data.get("found"):
            return None
        try:
            rx = int(data.get("rel_x"))
            ry = int(data.get("rel_y"))
            return rx, ry
        except Exception:
            return None

    def extract_position_available_volume(self, image_base64: str, code_6: str) -> int:
        """
        解析「持仓」表格裁剪图，返回指定代码的可用数量（可卖 / 可用）。
        """
        system_prompt = "你是持仓表格 OCR。只输出 JSON。"
        user_prompt = (
            f"图片为证券持仓列表。请找到股票代码为 {code_6} 的数据行，"
            "读取「可用数量」或「可卖数量」或「股票余额」列（以界面为准）。\n"
            "严格返回 JSON，例如找到时："
            '{"found": true, "available_volume": 1000}；找不到时：'
            '{"found": false, "available_volume": 0}'
        )
        raw = self._vlm_chat(system_prompt, user_prompt, image_base64, timeout=90)
        data = self._extract_json_object(raw)
        if not data.get("found"):
            return 0
        try:
            return max(0, int(float(str(data.get("available_volume", "0")).replace(",", ""))))
        except Exception:
            return 0

    def analyze_popup(self, image_base64: str) -> Dict[str, Any]:
        """
        【触发 AI 截图 2】判断是否被弹窗阻断。

        返回 dict: has_popup, content, is_blocking
        """
        system_prompt = "你是交易软件弹窗检测助手。只输出 JSON，不要其它文字。"
        user_prompt = (
            "请判断图片中是否存在警告、错误提示或系统升级弹窗？"
            "返回 JSON："
            '{"has_popup": true, "content": "资金不足", "is_blocking": true}'
        )
        raw = self._vlm_chat(system_prompt, user_prompt, image_base64)
        data = self._extract_json_object(raw)
        return {
            "has_popup": bool(data.get("has_popup")),
            "content": str(data.get("content") or ""),
            "is_blocking": bool(data.get("is_blocking")),
        }


def _norm_code(v: Any) -> str:
    s = re.sub(r"\D", "", str(v or ""))
    return s[-6:].zfill(6) if len(s) >= 6 else s.zfill(6) if s else ""


def _norm_price(v: Any) -> str:
    if v is None:
        return ""
    t = str(v or "").strip().lower()
    if t in ("", "market", "市价", "m"):
        return "market"
    try:
        return f"{float(t):.4f}".rstrip("0").rstrip(".")
    except Exception:
        return t
