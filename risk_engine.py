"""四层多源短临风险计算引擎。

本模块是气象决策的核心风险评估层，负责：
    1. 帧质量控制 — 检测坏帧、突变帧
    2. 双窗口趋势分析 — 3 帧（12min）敏感窗口 + 6 帧（30min）稳定窗口
    3. 上游回波检测 — 沿运动反方向追溯来向回波
    4. 四层融合风险评分 — QPF基线 + 雷达修正 + 地面环境 + 背景预报
    5. 标定日志 — 输出 JSONL 快照供回测使用

具体的"可打性评分"由 playability.py 负责，
"预约决策"由 booking_engine.py 负责。
本模块通过 re-export 将它们的公共接口统一暴露，
下游模块可继续使用 ``from risk_engine import XXX``。
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from config import (
    DBZ_NONE,
    DBZ_WEAK,
    DBZ_MODERATE,
    DBZ_STRONG,
    RH_LOW,
    RH_MID,
    RH_HIGH,
    TREND_WEIGHTS_6,
    TREND_WEIGHTS_3,
    RAIN_KEYWORDS,
    MIN_UPSTREAM_COVERAGE_25,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Re-export：将拆分后的子模块接口统一暴露
# ═══════════════════════════════════════════════════════════════════════════════

from playability import (  # noqa: F401
    compute_playability,
    heat_index_celsius,
    estimate_court_wetness,
)
from booking_engine import booking_decision  # noqa: F401


# ═══════════════════════════════════════════════════════════════════════════════
# 1. 帧质量控制
# ═══════════════════════════════════════════════════════════════════════════════


def frame_quality(
    dbz: np.ndarray, mask: np.ndarray, prev_dbz: np.ndarray | None = None
) -> float:
    """评估单帧雷达数据的质量 (0.0~1.0)。

    检测两类质量问题：
        - 全局信号突然消失（可能是坏帧或数据中断）
        - 局部 dBZ 突变（超过 25 dBZ 跳变，可能是伪回波）

    Args:
        dbz: 当前帧的 dBZ 二维数组。
        mask: 球场分析半径掩膜。
        prev_dbz: 上一帧的 dBZ 数组（首帧为 None）。

    Returns:
        质量分数，1.0 为正常，低于 0.5 视为坏帧。
    """
    score = 1.0
    total_pixels = mask.sum()
    if total_pixels == 0:
        return 0.0

    masked_vals = dbz[mask]

    # 全局回波检查
    global_echo = float((dbz > 0).sum())

    if prev_dbz is not None:
        prev_global = float((prev_dbz > 0).sum())
        # 全局信号骤降 80% 以上 → 疑似坏帧
        if prev_global > 100 and global_echo < prev_global * 0.2:
            score *= 0.4

        # 局部 dBZ 突变超过 25 → 疑似伪回波
        local_max = float(masked_vals.max()) if masked_vals.size else 0
        prev_local_max = float(prev_dbz[mask].max()) if prev_dbz[mask].size else 0
        if abs(local_max - prev_local_max) > 25:
            score *= 0.7

    return max(0.0, min(1.0, score))


# ═══════════════════════════════════════════════════════════════════════════════
# 2. 双窗口趋势分析
# ═══════════════════════════════════════════════════════════════════════════════


def _weighted_slope(values: list[float], weights: np.ndarray) -> float:
    """加权最小二乘线性斜率。正值=增强趋势。"""
    n = min(len(values), len(weights))
    if n < 2:
        return 0.0
    v = np.array(values[-n:])
    w = weights[-n:]
    x = np.arange(n, dtype=float)
    w_sum = w.sum()
    x_mean = np.sum(w * x) / w_sum
    v_mean = np.sum(w * v) / w_sum
    num = np.sum(w * (x - x_mean) * (v - v_mean))
    den = np.sum(w * (x - x_mean) ** 2)
    if abs(den) < 1e-9:
        return 0.0
    return float(num / den)


def compute_trends(
    frames_dbz: list[np.ndarray], mask: np.ndarray, quality_scores: list[float]
) -> dict[str, Any]:
    """双窗口趋势分析。

    同时计算两个时间窗口的趋势斜率：
        - trend_3: 最近 3 帧（~12 分钟），对快速增强/减弱敏感
        - trend_6: 最近 6 帧（~30 分钟），反映背景演变

    分析指标：max_dBZ、coverage_15（弱回波覆盖率）、coverage_25（强回波覆盖率）。
    质量分数 < 0.5 的坏帧会被排除。

    Returns:
        趋势字典，含各指标的 trend_3/trend_6 斜率值。
    """
    max_dbz_series = []
    cov15_series = []
    cov25_series = []

    for i, dbz in enumerate(frames_dbz):
        if quality_scores[i] < 0.5:
            continue
        vals = dbz[mask]
        if vals.size == 0:
            max_dbz_series.append(0.0)
            cov15_series.append(0.0)
            cov25_series.append(0.0)
        else:
            max_dbz_series.append(float(vals.max()))
            cov15_series.append(float((vals >= DBZ_NONE).mean()))
            cov25_series.append(float((vals >= DBZ_WEAK).mean()))

    def _trends(series: list[float]) -> dict:
        return {
            "trend_3": round(_weighted_slope(series, TREND_WEIGHTS_3), 4),
            "trend_6": round(_weighted_slope(series, TREND_WEIGHTS_6), 4),
        }

    bad_frame_count = sum(1 for q in quality_scores if q < 0.5)

    return {
        "max_dbz": _trends(max_dbz_series),
        "coverage_15": _trends(cov15_series),
        "coverage_25": _trends(cov25_series),
        "valid_frames": len(max_dbz_series),
        "bad_frames": bad_frame_count,
        "series_max_dbz": [round(v, 1) for v in max_dbz_series],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 3. 上游回波检测
# ═══════════════════════════════════════════════════════════════════════════════


def detect_upstream_echo(
    latest_dbz: np.ndarray, mask: np.ndarray, dx: float, dy: float, steps: int = 5
) -> dict[str, Any]:
    """沿运动反方向检测上游回波。

    将球场掩膜沿光流运动的**反方向**逐步回溯 steps 步，
    检查回溯路径上是否存在 ≥25 dBZ 的组织化回波。

    上游回波分级：
        trace      — 覆盖率 < 1%，孤立像素，不可操作
        weak       — 覆盖率 1-5%，小片回波，仅监控
        organized  — 覆盖率 ≥ 5%，有组织回波带

    Args:
        latest_dbz: 最新帧的 dBZ 数组。
        mask: 球场分析掩膜。
        dx, dy: 光流运动向量（像素/6分钟）。
        steps: 回溯步数（每步 6 分钟，默认 5 步 = 30 分钟）。
    """
    h, w = latest_dbz.shape
    upstream_mask = np.zeros_like(mask)
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return {"has_upstream_echo": False, "upstream_max_dbz": 0}

    # 沿运动反方向逐步回溯
    for step in range(1, steps + 1):
        ux = np.clip((xs - dx * step).astype(int), 0, w - 1)
        uy = np.clip((ys - dy * step).astype(int), 0, h - 1)
        upstream_mask[uy, ux] = True

    upstream_vals = latest_dbz[upstream_mask]
    if upstream_vals.size == 0:
        return {"has_upstream_echo": False, "upstream_max_dbz": 0}

    max_up = float(upstream_vals.max())
    cov_25 = float((upstream_vals >= DBZ_WEAK).mean())

    # 分级
    if cov_25 < 0.01:
        upstream_level = "trace"
    elif cov_25 < 0.05:
        upstream_level = "weak"
    else:
        upstream_level = "organized"

    has_upstream = max_up >= DBZ_WEAK and cov_25 >= MIN_UPSTREAM_COVERAGE_25

    return {
        "has_upstream_echo": has_upstream,
        "upstream_max_dbz": int(round(max_up)),
        "upstream_coverage_25": round(cov_25, 4),
        "upstream_level": upstream_level,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 4. 四层融合风险评分
# ═══════════════════════════════════════════════════════════════════════════════


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> int:
    """将浮点值截断到 [lo, hi] 并取整。"""
    return int(round(max(lo, min(hi, v))))


def compute_risk_scores(
    current_stats: dict[str, float],
    rain_probability: dict[str, float],
    trends: dict[str, Any],
    upstream: dict[str, Any],
    grid_realtime: dict[str, Any],
    qpf6min_all_zero: bool,
    rain_flag: int,
    motion_consistency: float,
    hourly_forecast: list[dict],
    radar_visual_qa: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """四层融合风险评分引擎。

    四层结构：
        Layer 1 — 官方 QPF 基线（0-65 分，由 QPF + rainFlag 一致性决定）
        Layer 2 — 雷达修正（±30 分，含强度/趋势/上游/视觉QA）
        Layer 3 — 地面环境（±35 分，含湿度/实况降雨硬覆盖）
        Layer 4 — 背景预报（±10 分，逐小时预报降雨小时数）

    各时段权重配比：
        now:   QPF×0.4 + 雷达×1.0 + 地面×1.0 + 背景×0.3
        30min: QPF×0.6 + 雷达×0.8 + 地面×0.6 + 背景×0.3
        60min: QPF×0.7 + 雷达×0.6 + 地面×0.5 + 背景×0.5
        120min:QPF×0.8 + 雷达×0.4 + 地面×0.4 + 背景×0.8

    Returns:
        风险评分字典，含 now_risk/risk_30/risk_60/risk_120、
        conclusion（结论）、conflicts（冲突列表）和 layer_detail（层级明细）。
    """
    humidity = grid_realtime.get("humidity_pct", 75.0) or 75.0
    weather = grid_realtime.get("weather_state", "") or ""
    max_dbz = current_stats.get("max_dbz", 0)
    cov25 = current_stats.get("playable_coverage", 0)

    # 多模态视觉 QA 信号
    visual = radar_visual_qa or {}
    visual_quality = visual.get("quality")
    echo_pattern = visual.get("echo_pattern")
    visual_adjust = visual.get("radar_confidence_adjustment")
    motion_readable = visual.get("motion_readable")
    upstream_signal = visual.get("upstream_signal")

    visual_suppresses_radar = (
        visual_quality == "bad"
        or visual_adjust == "down"
        or echo_pattern in {"none", "trace", "scattered_weak"}
        or motion_readable is False
    )
    visual_supports_organized_upstream = (
        echo_pattern in {"organized_band", "convective_cells"}
        and upstream_signal == "organized"
        and visual_quality in {None, "good", "degraded"}
    )

    # ---- Layer 1: 官方 QPF 基线 ----
    if qpf6min_all_zero and rain_flag == 0:
        qpf_base = 5  # 双源一致无雨
        # 雷达覆盖检查：QPF 无雨但雷达有明显回波时上调
        if not visual_suppresses_radar and max_dbz >= DBZ_MODERATE:
            qpf_base = 45
        elif not visual_suppresses_radar and max_dbz >= DBZ_WEAK:
            qpf_base = 25
    elif not qpf6min_all_zero and rain_flag == 1:
        qpf_base = 65  # 双源一致有雨
    elif not qpf6min_all_zero and rain_flag == 0:
        qpf_base = 35  # QPF 有雨，secondary 无雨
    elif qpf6min_all_zero and rain_flag == 1:
        qpf_base = 35  # QPF 无雨，secondary 有雨
    else:
        qpf_base = 30

    # ---- Layer 2: 雷达修正 ----
    radar_mod = 0
    if max_dbz >= DBZ_STRONG:
        radar_mod += 30
    elif max_dbz >= DBZ_MODERATE:
        radar_mod += 20
    elif max_dbz >= DBZ_WEAK:
        radar_mod += 10

    # 覆盖率趋势
    t3_cov = trends.get("coverage_25", {}).get("trend_3", 0)
    t6_cov = trends.get("coverage_25", {}).get("trend_6", 0)
    if t3_cov > 0 and t6_cov > 0:
        radar_mod += 10  # 持续发展
    elif t3_cov < 0 and t6_cov < 0:
        radar_mod -= 10  # 持续减弱
    elif t3_cov > 0 and t6_cov < 0:
        radar_mod += 5   # 可能再发展

    # 上游回波（按覆盖率分级加分）
    if upstream.get("has_upstream_echo"):
        up_cov = upstream.get("upstream_coverage_25", 0)
        up_max = upstream.get("upstream_max_dbz", 0)
        if up_cov >= 0.10:
            radar_mod += min(15, up_max // 3)
        elif up_cov >= 0.03:
            radar_mod += min(8, up_max // 5)
        elif up_cov >= 0.01:
            radar_mod += 2

    # 视觉 QA 对雷达证据的置信度调整
    if echo_pattern in {"none", "trace", "scattered_weak"}:
        radar_mod = min(radar_mod, 4)
    if visual_quality == "bad":
        radar_mod = 0
    elif visual_adjust == "down" or motion_readable is False:
        radar_mod = int(radar_mod * 0.35)
    elif visual_supports_organized_upstream:
        radar_mod += 6

    # 运动一致性折扣
    if motion_consistency < 0.4:
        radar_mod = int(radar_mod * 0.5)

    # ---- Layer 3: 地面环境 ----
    surface_mod = 0
    if humidity > RH_HIGH:
        surface_mod += 10
    elif humidity > RH_MID:
        surface_mod += 5
    elif humidity < RH_LOW:
        surface_mod -= 8

    # 实况降雨硬覆盖
    if (
        any(kw in weather for kw in RAIN_KEYWORDS)
        or grid_realtime.get("rain_5m_mm", 0) > 0
        or grid_realtime.get("rain_1h_mm", 0) > 0
    ):
        surface_mod += 35

    # ---- Layer 4: 背景预报 ----
    bg_mod = 0
    near_hours = hourly_forecast[:6] if hourly_forecast else []
    rain_hours = sum(
        1 for h in near_hours if any(kw in h.get("weather", "") for kw in RAIN_KEYWORDS)
    )
    if rain_hours >= 3:
        bg_mod += 10
    elif rain_hours >= 1:
        bg_mod += 5

    # ---- 各时段加权计算 ----
    now_risk = _clamp(qpf_base * 0.4 + radar_mod * 1.0 + surface_mod * 1.0 + bg_mod * 0.3)
    r30_radar = radar_mod + rain_probability.get("30min", 0) * 30
    risk_30 = _clamp(qpf_base * 0.6 + r30_radar * 0.8 + surface_mod * 0.6 + bg_mod * 0.3)
    r60_radar = radar_mod + rain_probability.get("60min", 0) * 25
    risk_60 = _clamp(qpf_base * 0.7 + r60_radar * 0.6 + surface_mod * 0.5 + bg_mod * 0.5)
    r120_radar = radar_mod * 0.3 + rain_probability.get("120min", 0) * 15
    risk_120 = _clamp(qpf_base * 0.8 + r120_radar * 0.4 + surface_mod * 0.4 + bg_mod * 0.8)

    # 特殊场景：仅雷达有回波但所有其他源均无雨 → 限制上限
    surface_has_rain = (
        any(kw in weather for kw in RAIN_KEYWORDS)
        or grid_realtime.get("rain_5m_mm", 0) > 0
        or grid_realtime.get("rain_1h_mm", 0) > 0
    )
    radar_only_context = qpf6min_all_zero and rain_flag == 0 and not surface_has_rain
    if radar_only_context and visual_supports_organized_upstream:
        now_risk = min(now_risk, 40)
        risk_30 = min(risk_30, 60)
        risk_60 = min(risk_60, 55)
        risk_120 = min(risk_120, 45)

    # ---- 结论映射 ----
    if now_risk > 70:
        conclusion, conclusion_cn = "seek_shelter", "立即避雨"
    elif risk_30 > 60 or now_risk > 40:
        conclusion, conclusion_cn = "not_recommended", "不建议开打"
    elif now_risk > 25 or (25 < risk_30 <= 60):
        conclusion, conclusion_cn = "cautious", "谨慎可打"
    else:
        conclusion, conclusion_cn = "playable", "可打"

    # ---- 冲突检测 ----
    conflicts = []
    if qpf6min_all_zero and rain_probability.get("30min", 0) > 0.3:
        conflicts.append("radar_echo_but_qpf_clear")
    if not qpf6min_all_zero and max_dbz < DBZ_NONE:
        conflicts.append("qpf_rain_but_radar_clear")
    if (not qpf6min_all_zero) != (rain_flag == 1):
        conflicts.append("qpf_rainflag_disagree")
    if any(kw in weather for kw in RAIN_KEYWORDS) and max_dbz < DBZ_NONE and qpf6min_all_zero:
        conflicts.append("weather_state_rain_but_no_radar_no_qpf")
    if visual_quality == "bad":
        conflicts.append("radar_visual_quality_bad")
    elif visual_adjust == "down" or motion_readable is False:
        conflicts.append("radar_visual_confidence_low")

    return {
        "now_risk": now_risk,
        "risk_30": risk_30,
        "risk_60": risk_60,
        "risk_120": risk_120,
        "conclusion": conclusion,
        "conclusion_cn": conclusion_cn,
        "conflicts": conflicts,
        "layer_detail": {
            "qpf_base": qpf_base,
            "radar_mod": radar_mod,
            "radar_visual_adjustment": visual_adjust or "neutral",
            "surface_mod": surface_mod,
            "background_mod": bg_mod,
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 5. 标定日志
# ═══════════════════════════════════════════════════════════════════════════════


def save_calibration_log(
    report: dict[str, Any],
    risk_scores: dict[str, Any],
    log_path: str = "output/calibration_log.jsonl",
) -> None:
    """追加一条 JSONL 快照，用于日后的回测和模型校准。

    每条记录包含当时的输入特征和输出风险评分，
    以及两个待人工填写的实况标签（actual_rain_30min / actual_rain_60min）。
    """
    entry = {
        "timestamp": report.get("generated_at"),
        "max_dbz": report.get("current", {}).get("max_dbz_nearby", 0),
        "echo_coverage": report.get("current", {}).get("echo_coverage", 0),
        "playable_coverage": report.get("current", {}).get("playable_coverage", 0),
        "rain_prob_30": report.get("rain_probability", {}).get("30min", 0),
        "rain_prob_60": report.get("rain_probability", {}).get("60min", 0),
        "rain_prob_120": report.get("rain_probability", {}).get("120min", 0),
        "motion_consistency": report.get("motion", {}).get("consistency", 0),
        "qpf_summary": report.get("official_qpf6min_summary", ""),
        "humidity": report.get("station_realtime", {}).get("humidity_pct"),
        "temperature": report.get("station_realtime", {}).get("temperature"),
        "weather_state": report.get("station_realtime", {}).get("weather_state"),
        "wind_speed": report.get("station_realtime", {}).get("wind_speed_mps"),
        "rain_flag": report.get("station_realtime", {}).get("rain_2h_flag"),
        "radar_visual_qa": report.get("radar_visual_qa"),
        "trends": report.get("trends"),
        "risk_scores": {
            "now": risk_scores.get("now_risk"),
            "r30": risk_scores.get("risk_30"),
            "r60": risk_scores.get("risk_60"),
            "r120": risk_scores.get("risk_120"),
            "conclusion": risk_scores.get("conclusion"),
        },
        "actual_rain_30min": None,  # 待人工回填
        "actual_rain_60min": None,  # 待人工回填
    }
    p = Path(log_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
