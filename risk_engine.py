"""Four-layer multi-source nowcast decision engine.

Implements frame quality control, dual-window trend analysis,
upstream echo detection, risk score computation, and calibration logging.
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
    PLAYABILITY_BASE_WEIGHTS,
    PLAYABILITY_GRADE_TABLE,
)


# ---- 1. Frame Quality Control ----


def frame_quality(
    dbz: np.ndarray, mask: np.ndarray, prev_dbz: np.ndarray | None = None
) -> float:
    """Return 0.0-1.0 quality score for a single radar frame."""
    score = 1.0
    total_pixels = mask.sum()
    if total_pixels == 0:
        return 0.0

    # Check if entire masked region is transparent/zero
    masked_vals = dbz[mask]
    nonzero_ratio = float((masked_vals > 0).sum()) / max(1, total_pixels)

    # Global echo check: if entire image has almost no signal, could be bad frame
    global_echo = float((dbz > 0).sum())
    global_ratio = global_echo / max(1, dbz.size)

    # If global image is nearly empty but previous wasn't, suspicious
    if prev_dbz is not None:
        prev_global = float((prev_dbz > 0).sum())
        if prev_global > 100 and global_echo < prev_global * 0.2:
            score *= 0.4  # Sudden drop, likely bad frame

        # Local sudden jump without spatial continuity
        local_max = float(masked_vals.max()) if masked_vals.size else 0
        prev_local_max = float(prev_dbz[mask].max()) if prev_dbz[mask].size else 0
        if abs(local_max - prev_local_max) > 25:
            score *= 0.7  # Suspicious jump

    return max(0.0, min(1.0, score))


# ---- 2. Dual-Window Trend Analysis ----


def _weighted_slope(values: list[float], weights: np.ndarray) -> float:
    """Compute weighted linear slope. Positive = increasing."""
    n = min(len(values), len(weights))
    if n < 2:
        return 0.0
    v = np.array(values[-n:])
    w = weights[-n:]
    x = np.arange(n, dtype=float)
    # Weighted least squares
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
    """Compute dual-window trends for max_dBZ, coverage_15, coverage_25.

    Returns trend_3 (12min, sensitive) and trend_6 (30min, stable).
    Bad frames (quality < 0.5) are excluded.
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


# ---- 3. Upstream Echo Detection ----


def detect_upstream_echo(
    latest_dbz: np.ndarray, mask: np.ndarray, dx: float, dy: float, steps: int = 5
) -> dict[str, Any]:
    """Check if ≥25dBZ echo exists upstream along the motion vector (30min path)."""
    h, w = latest_dbz.shape
    # Upstream = reverse of motion direction
    upstream_mask = np.zeros_like(mask)
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return {"has_upstream_echo": False, "upstream_max_dbz": 0}

    for step in range(1, steps + 1):
        ux = np.clip((xs - dx * step).astype(int), 0, w - 1)
        uy = np.clip((ys - dy * step).astype(int), 0, h - 1)
        upstream_mask[uy, ux] = True

    upstream_vals = latest_dbz[upstream_mask]
    if upstream_vals.size == 0:
        return {"has_upstream_echo": False, "upstream_max_dbz": 0}

    max_up = float(upstream_vals.max())
    cov_25 = float((upstream_vals >= DBZ_WEAK).mean())

    # Classify upstream echo level by coverage
    if cov_25 < 0.01:
        upstream_level = "trace"  # Isolated pixel, not actionable
    elif cov_25 < 0.05:
        upstream_level = "weak"  # Small patch, monitor only
    else:
        upstream_level = "organized"  # Significant echo band

    has_upstream = max_up >= DBZ_WEAK and cov_25 >= MIN_UPSTREAM_COVERAGE_25

    return {
        "has_upstream_echo": has_upstream,
        "upstream_max_dbz": int(round(max_up)),
        "upstream_coverage_25": round(cov_25, 4),
        "upstream_level": upstream_level,
    }


# ---- 4. Risk Score Computation ----


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> int:
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
    """Four-layer fusion engine producing risk scores + conclusion."""

    humidity = grid_realtime.get("humidity_pct", 75.0) or 75.0
    weather = grid_realtime.get("weather_state", "") or ""
    max_dbz = current_stats.get("max_dbz", 0)
    cov25 = current_stats.get("playable_coverage", 0)
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

    # ---- Layer 1: Official QPF base risk ----
    if qpf6min_all_zero and rain_flag == 0:
        qpf_base = 5  # Both agree: no rain
        # Radar override: when radar shows obvious echo but QPF says no rain
        if not visual_suppresses_radar and max_dbz >= DBZ_MODERATE:
            qpf_base = 45
        elif not visual_suppresses_radar and max_dbz >= DBZ_WEAK:
            qpf_base = 25
    elif not qpf6min_all_zero and rain_flag == 1:
        qpf_base = 65  # Both agree: rain
    elif not qpf6min_all_zero and rain_flag == 0:
        qpf_base = 35  # QPF says rain, secondary says no
    elif qpf6min_all_zero and rain_flag == 1:
        qpf_base = 35  # QPF says no rain, secondary says rain
    else:
        qpf_base = 30

    # ---- Layer 2: Radar modification ----
    radar_mod = 0
    if max_dbz >= DBZ_STRONG:
        radar_mod += 30
    elif max_dbz >= DBZ_MODERATE:
        radar_mod += 20
    elif max_dbz >= DBZ_WEAK:
        radar_mod += 10

    # Coverage trend
    t3_cov = trends.get("coverage_25", {}).get("trend_3", 0)
    t6_cov = trends.get("coverage_25", {}).get("trend_6", 0)
    if t3_cov > 0 and t6_cov > 0:
        radar_mod += 10  # Sustained development
    elif t3_cov < 0 and t6_cov < 0:
        radar_mod -= 10  # Sustained weakening
    elif t3_cov > 0 and t6_cov < 0:
        radar_mod += 5  # Possible re-development

    # Upstream echo (coverage-gated)
    if upstream.get("has_upstream_echo"):
        up_cov = upstream.get("upstream_coverage_25", 0)
        up_max = upstream.get("upstream_max_dbz", 0)
        if up_cov >= 0.10:
            radar_mod += min(15, up_max // 3)
        elif up_cov >= 0.03:
            radar_mod += min(8, up_max // 5)
        elif up_cov >= 0.01:
            radar_mod += 2

    # Multimodal visual QA only adjusts radar evidence confidence.
    if echo_pattern in {"none", "trace", "scattered_weak"}:
        radar_mod = min(radar_mod, 4)
    if visual_quality == "bad":
        radar_mod = 0
    elif visual_adjust == "down" or motion_readable is False:
        radar_mod = int(radar_mod * 0.35)
    elif visual_supports_organized_upstream:
        radar_mod += 6

    # Motion consistency discount
    if motion_consistency < 0.4:
        radar_mod = int(radar_mod * 0.5)

    # ---- Layer 3: Surface environment ----
    surface_mod = 0
    if humidity > RH_HIGH:
        surface_mod += 10
    elif humidity > RH_MID:
        surface_mod += 5
    elif humidity < RH_LOW:
        surface_mod -= 8

    # Hard override for realtime rain
    if (
        any(kw in weather for kw in RAIN_KEYWORDS)
        or grid_realtime.get("rain_5m_mm", 0) > 0
        or grid_realtime.get("rain_1h_mm", 0) > 0
    ):
        surface_mod += 35

    # ---- Layer 4: Background ----
    bg_mod = 0
    near_hours = hourly_forecast[:6] if hourly_forecast else []
    rain_hours = sum(
        1 for h in near_hours if any(kw in h.get("weather", "") for kw in RAIN_KEYWORDS)
    )
    if rain_hours >= 3:
        bg_mod += 10
    elif rain_hours >= 1:
        bg_mod += 5

    # ---- Compute per-horizon risk ----
    # now_risk: current frame + trend_3 dominant
    now_risk = _clamp(
        qpf_base * 0.4 + radar_mod * 1.0 + surface_mod * 1.0 + bg_mod * 0.3
    )

    # risk_30: QPF highest, radar+trend_3 secondary
    r30_radar = radar_mod + rain_probability.get("30min", 0) * 30
    risk_30 = _clamp(
        qpf_base * 0.6 + r30_radar * 0.8 + surface_mod * 0.6 + bg_mod * 0.3
    )

    # risk_60: QPF highest, trend_6 more weight
    r60_radar = radar_mod + rain_probability.get("60min", 0) * 25
    risk_60 = _clamp(
        qpf_base * 0.7 + r60_radar * 0.6 + surface_mod * 0.5 + bg_mod * 0.5
    )

    # risk_120: QPF dominant, radar only auxiliary
    r120_radar = radar_mod * 0.3 + rain_probability.get("120min", 0) * 15
    risk_120 = _clamp(
        qpf_base * 0.8 + r120_radar * 0.4 + surface_mod * 0.4 + bg_mod * 0.8
    )

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

    # ---- Conclusion ----
    if now_risk > 70:
        conclusion = "seek_shelter"
        conclusion_cn = "立即避雨"
    elif risk_30 > 60 or now_risk > 40:
        conclusion = "not_recommended"
        conclusion_cn = "不建议开打"
    elif now_risk > 25 or (25 < risk_30 <= 60):
        conclusion = "cautious"
        conclusion_cn = "谨慎可打"
    else:
        conclusion = "playable"
        conclusion_cn = "可打"

    # ---- Conflicts ----
    conflicts = []
    if qpf6min_all_zero and rain_probability.get("30min", 0) > 0.3:
        conflicts.append("radar_echo_but_qpf_clear")
    if not qpf6min_all_zero and max_dbz < DBZ_NONE:
        conflicts.append("qpf_rain_but_radar_clear")
    if (not qpf6min_all_zero) != (rain_flag == 1):
        conflicts.append("qpf_rainflag_disagree")
    if (
        any(kw in weather for kw in RAIN_KEYWORDS)
        and max_dbz < DBZ_NONE
        and qpf6min_all_zero
    ):
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


# ---- 5. Calibration Logging ----


def save_calibration_log(
    report: dict[str, Any],
    risk_scores: dict[str, Any],
    log_path: str = "output/calibration_log.jsonl",
) -> None:
    """Append one JSON-line snapshot for future backtesting."""
    entry = {
        "timestamp": report.get("generated_at"),
        # Inputs
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
        # Trends
        "trends": report.get("trends"),
        # Outputs
        "risk_scores": {
            "now": risk_scores.get("now_risk"),
            "r30": risk_scores.get("risk_30"),
            "r60": risk_scores.get("risk_60"),
            "r120": risk_scores.get("risk_120"),
            "conclusion": risk_scores.get("conclusion"),
        },
        # Post-hoc label (to be filled manually or by later script)
        "actual_rain_30min": None,
        "actual_rain_60min": None,
    }
    p = Path(log_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---- 6. Booking Decision Engine ----


def booking_decision(
    risk_scores: dict[str, Any],
    lead_time_hours: float,
    target_time_str: str,
    play_duration_minutes: int,
    hourly_forecast: list[dict],
    seven_day_forecast: list[dict],
    qpf6min_all_zero: bool,
    rain_flag: int,
    grid_realtime: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Produce a booking-specific decision based on lead time band.

    Lead time bands:
      0-2h  → Full nowcast, output playability verdict
      2-6h  → Reduced radar weight, output keep/watch/prepare + check_again_at
      6h+   → Background risk only, output recheck schedule
    """
    if now is None:
        now = datetime.now()

    # Determine lead time band
    if lead_time_hours <= 2:
        band = "0-2h"
    elif lead_time_hours <= 6:
        band = "2-6h"
    else:
        band = "6h+"

    # Parse target hour for matching hourly forecast
    try:
        target_hour = (
            int(target_time_str.split(":")[0]) if ":" in target_time_str else -1
        )
    except (ValueError, IndexError):
        target_hour = -1

    # Scan play window in hourly forecast for rain keywords
    play_hours = max(1, play_duration_minutes // 60)
    window_rain_count = 0
    pre_window_rain = False  # Rain before target that could wet the court

    for h in hourly_forecast:
        h_time = h.get("time", "")
        h_weather = h.get("weather", "")
        try:
            h_hour = int(h_time.split(":")[0]) if ":" in h_time else -1
        except (ValueError, IndexError):
            h_hour = -1

        if h_hour < 0 or target_hour < 0:
            continue

        # Normalize for cross-midnight comparison
        h_norm = h_hour if h_hour >= 12 or target_hour < 12 else h_hour + 24
        t_norm = target_hour if target_hour >= 12 or h_hour < 12 else target_hour + 24

        # Check 2 hours before target (court pre-wetting)
        if t_norm - 2 <= h_norm < t_norm:
            if any(kw in h_weather for kw in RAIN_KEYWORDS):
                pre_window_rain = True

        # Check play window
        if t_norm <= h_norm < t_norm + play_hours:
            if any(kw in h_weather for kw in RAIN_KEYWORDS):
                window_rain_count += 1

    # Check today's daily forecast for rain background
    today_rain_bg = False
    if seven_day_forecast:
        for d in seven_day_forecast[:2]:
            label = d.get("label", "")
            if "今天" in label or "今日" in label:
                for field in ["weather_day", "weather_night"]:
                    if any(kw in d.get(field, "") for kw in RAIN_KEYWORDS):
                        today_rain_bg = True

    # Build reasons list
    reasons = []
    caveats = []

    # Check for realtime raining
    grid_weather = grid_realtime.get("weather_state", "")
    is_raining_now = (
        any(kw in grid_weather for kw in RAIN_KEYWORDS)
        or grid_realtime.get("rain_5m_mm", 0) > 0
        or grid_realtime.get("rain_1h_mm", 0) > 0
    )

    # ---- Band-specific decision logic ----
    if band == "0-2h":
        # Direct nowcast: use existing risk_scores conclusion
        conclusion = risk_scores.get("conclusion", "playable")
        conclusion_cn = risk_scores.get("conclusion_cn", "可打")

        decision_map = {
            "playable": ("keep_booking", "保留预约，可以出发"),
            "cautious": ("keep_but_recheck", "保留预约，谨慎出发"),
            "not_recommended": ("suggest_cancel", "建议取消或改期"),
            "seek_shelter": ("suggest_cancel", "建议取消"),
        }
        decision, decision_cn = decision_map.get(
            conclusion, ("keep_booking", "保留预约")
        )

        if is_raining_now:
            decision = "suggest_cancel"
            decision_cn = "建议取消或改期"
            reasons.append(f"当前实况为「{grid_weather}」，场地可能已经湿滑")

        if qpf6min_all_zero and rain_flag == 0:
            if is_raining_now:
                reasons.append(
                    f"官方短临显示未来2小时无雨，但当前实况已有降水，存在矛盾"
                )
            else:
                reasons.append("官方短临一致支持未来2小时无雨")
        if risk_scores.get("now_risk", 0) < 25 and not is_raining_now:
            reasons.append("当前降雨风险很低")
        if window_rain_count > 0:
            reasons.append(f"逐小时预报显示打球时段有{window_rain_count}小时可能有雨")
            if decision == "keep_booking":
                decision = "keep_but_recheck"
                decision_cn = "保留预约，赛前关注变化"
        if pre_window_rain:
            reasons.append("开场前2小时有降雨预报，球场可能提前变湿")
            caveats.append("即使开场时雨停，场地可能仍有积水或湿滑")

        # check_again_at: 15 min before target
        from datetime import timedelta

        check_at = now + timedelta(minutes=max(15, lead_time_hours * 60 - 15))
        check_again = check_at.strftime("%H:%M")

    elif band == "2-6h":
        # Short-term: radar weight reduced, hourly forecast dominant
        reasons.append(f"距开场约{lead_time_hours:.1f}小时，雷达外推参考价值有限")

        if window_rain_count == 0 and not pre_window_rain:
            if qpf6min_all_zero and rain_flag == 0:
                decision = "keep_but_recheck"
                decision_cn = "建议保留预约，赛前复查"
                reasons.append("逐小时预报打球时段无雨")
                reasons.append("当前官方短临未来2小时无雨")
            elif today_rain_bg:
                decision = "wait_and_see"
                decision_cn = "建议观望"
                reasons.append("当天有阵雨/雷阵雨背景，局地新生风险不能排除")
            else:
                decision = "keep_but_recheck"
                decision_cn = "建议保留预约，赛前复查"
        elif window_rain_count >= 2:
            decision = "suggest_cancel"
            decision_cn = "建议取消或改期"
            reasons.append(f"逐小时预报打球时段有{window_rain_count}小时有雨")
        else:
            decision = "wait_and_see"
            decision_cn = "建议观望"
            reasons.append("打球时段有零星降雨预报")

        if pre_window_rain:
            reasons.append("开场前可能有降雨，球场有被淋湿风险")
            caveats.append("即使开场时雨停，场地可能仍有积水或湿滑")

        # check_again_at: target - 45min, but no earlier than now + 30min
        from datetime import timedelta

        ideal_check = now + timedelta(hours=lead_time_hours - 0.75)
        earliest = now + timedelta(minutes=30)
        check_at = max(ideal_check, earliest)
        check_again = check_at.strftime("%H:%M")

        caveats.append(
            f"若{check_again}后QPF出现非零降雨或上游出现≥25dBZ回波，应重新评估"
        )

    else:  # 6h+
        decision = "background_only"
        decision_cn = "仅供背景参考，距开场较远"
        reasons.append(f"距开场约{lead_time_hours:.1f}小时，短临数据不适用于此时段判断")

        if today_rain_bg:
            reasons.append("当天预报有阵雨/雷阵雨背景")
            decision = "wait_and_see"
            decision_cn = "天气背景偏活跃，建议持续关注"
        else:
            reasons.append("当天预报无明显降雨背景")

        if window_rain_count > 0:
            reasons.append(f"逐小时预报打球时段有{window_rain_count}小时可能有雨")

        # Schedule two rechecks
        from datetime import timedelta

        recheck1 = now + timedelta(hours=max(1, lead_time_hours - 2.5))
        recheck2 = now + timedelta(hours=max(2, lead_time_hours - 0.75))
        check_again = f"{recheck1.strftime('%H:%M')} 和 {recheck2.strftime('%H:%M')}"

        caveats.append("超过6小时的预判以天气背景为主，不宜作为最终取消依据")

    # Compute play window string
    try:
        t_parts = target_time_str.split(":")
        t_h, t_m = int(t_parts[0]), int(t_parts[1]) if len(t_parts) > 1 else 0
        end_h = t_h + play_duration_minutes // 60
        end_m = t_m + play_duration_minutes % 60
        if end_m >= 60:
            end_h += 1
            end_m -= 60
        end_h = end_h % 24
        play_window = f"{target_time_str}-{end_h:02d}:{end_m:02d}"
    except (ValueError, IndexError):
        play_window = f"{target_time_str}-?"

    return {
        "target_time": target_time_str,
        "play_window": play_window,
        "play_duration_minutes": play_duration_minutes,
        "lead_time_hours": round(lead_time_hours, 1),
        "lead_time_band": band,
        "decision": decision,
        "decision_cn": decision_cn,
        "check_again_at": check_again,
        "reason": reasons,
        "caveat": caveats if caveats else ["无特别注意事项"],
        "window_hourly_rain_count": window_rain_count,
        "pre_window_rain_risk": pre_window_rain,
        "today_rain_background": today_rain_bg,
    }


# ---- 7. Playability Scoring System ----


def heat_index_celsius(t_c: float, rh: float) -> float:
    """Compute Heat Index (apparent temperature) using Rothfusz regression.

    Only applicable when T >= 27°C and RH >= 40%.  For other conditions
    the dry-bulb temperature is returned directly.
    """
    if t_c < 27 or rh < 40:
        return t_c
    t_f = t_c * 9.0 / 5.0 + 32.0
    hi_f = (
        -42.379
        + 2.04901523 * t_f
        + 10.14333127 * rh
        - 0.22475541 * t_f * rh
        - 6.83783e-3 * t_f**2
        - 5.481717e-2 * rh**2
        + 1.22874e-3 * t_f**2 * rh
        + 8.5282e-4 * t_f * rh**2
        - 1.99e-6 * t_f**2 * rh**2
    )
    return (hi_f - 32.0) * 5.0 / 9.0


def _check_veto(
    weather_state: str,
    qpf_has_rain: bool,
    rain_flag: int,
    max_dbz: float,
    playable_coverage: float,
    apparent_temp: float | None,
    wind_speed: float | None,
    aqi: float | None,
) -> tuple[bool, str | None]:
    """Veto layer — hard constraints that set playability to 0.

    Returns (is_vetoed, reason_cn).
    """
    # V1: Rain veto is now handled dynamically per-horizon based on
    # QPF, Radar, and the Evaporation model. We no longer globally veto
    # all future horizons just because it's raining right now.

    # V2: Triple-source agreement on rain + strong radar echo
    if (
        qpf_has_rain
        and rain_flag == 1
        and max_dbz >= DBZ_MODERATE
        and playable_coverage >= 0.05
    ):
        return True, "多个气象源一致预报短时有较强降雨"

    # V3: Extreme heat (Heat Index >= 42°C)
    if apparent_temp is not None and apparent_temp >= 42:
        return True, f"体感温度过高（{apparent_temp:.0f}°C），存在中暑风险"

    # V4: High wind (>= 14 m/s ≈ 50 km/h)
    if wind_speed is not None and wind_speed >= 14:
        return True, f"风力过大（{wind_speed:.1f}m/s），无法正常打球"

    # V5: Severe air pollution (AQI >= 201)
    if aqi is not None and aqi >= 201:
        return True, f"空气质量严重不佳（AQI {int(aqi)}），不建议户外运动"

    return False, None


import math

def estimate_court_wetness(
    rain_1h_mm: float,
    rain_5m_mm: float,
    is_raining_now: bool,
    temp: float | None,
    humidity: float | None,
    wind_speed: float | None,
    horizon_hours: float,
) -> float:
    """Semi-physical exponential decay state model for court wetness (0.0 to 1.0)."""
    rain_input = (
        0.35 * min(1.0, rain_5m_mm / 1.0)
        + 0.45 * min(1.0, rain_1h_mm / 5.0)
        + 0.20 * float(is_raining_now)
    )
    wetness_now = min(1.0, rain_input)
    t = temp if temp is not None else 20.0
    rh = humidity if humidity is not None else 75.0
    w = wind_speed if wind_speed is not None else 0.0
    
    temp_factor = 1.0 + 0.03 * max(0.0, t - 20.0) - 0.025 * max(0.0, 20.0 - t)
    wind_factor = 1.0 + 0.12 * min(max(w, 0.0), 8.0)
    humidity_factor = 1.0 - 0.006 * max(0.0, rh - 60.0)
    
    drying_rate = 0.85 * temp_factor * wind_factor * humidity_factor
    drying_rate = max(0.15, min(1.8, drying_rate))
    
    wetness = wetness_now * math.exp(-drying_rate * horizon_hours)
    return max(0.0, min(1.0, wetness))


def _score_rain(
    rain_prob: float,
    qpf_has_rain: bool,
    rain_flag: int,
    rain_1h_mm: float,
    rain_5m_mm: float,
    weather_state: str,
    temp: float | None,
    humidity: float | None,
    wind_speed: float | None,
    horizon_hours: float,
) -> tuple[int, str, int, bool, str | None]:
    """Rain sub-score (0-100) with dynamic exponential wetness state model.

    Fuses independent future rain signals and applies a penalty based on
    a continuous court wetness state that decays over time depending on weather.
    Also returns a (vetoed, veto_reason) tuple if rain/wetness makes it strictly unplayable at this horizon.
    """
    signal = (
        0.50 * rain_prob
        + 0.30 * (1.0 if qpf_has_rain else 0.0)
        + 0.20 * (1.0 if rain_flag == 1 else 0.0)
    )
    if signal <= 0.05:
        s = 100
    elif signal <= 0.10:
        s = 92
    elif signal <= 0.20:
        s = 78
    elif signal <= 0.35:
        s = 58
    elif signal <= 0.50:
        s = 38
    elif signal <= 0.70:
        s = 18
    else:
        s = 5

    # Human-readable base description
    if s >= 90:
        desc = "预报显示无降雨，天气晴好"
    elif s >= 70:
        desc = "有微弱降雨信号，场地可能微湿"
    elif s >= 50:
        desc = "有一定降雨可能，需留意场地湿滑风险"
    elif s >= 30:
        desc = "降雨可能性较大，场地极大概率会积水"
    else:
        desc = "降雨可能性很高，场地湿滑不适合运动"

    # Semi-physical Wetness State Model
    RAIN_KEYWORDS = ["雨", "雪", "雹", "冰"]
    is_raining_now = rain_5m_mm > 0 or any(kw in weather_state for kw in RAIN_KEYWORDS)
    
    wetness = estimate_court_wetness(
        rain_1h_mm=rain_1h_mm,
        rain_5m_mm=rain_5m_mm,
        is_raining_now=is_raining_now,
        temp=temp,
        humidity=humidity,
        wind_speed=wind_speed,
        horizon_hours=horizon_hours,
    )

    penalty = int(round(80 * wetness))
    s = max(0, s - penalty)
    
    vetoed = False
    veto_reason = None

    if wetness >= 0.75:
        vetoed = True
        veto_reason = "场地严重湿滑或积水，极易滑倒"
    elif is_raining_now and horizon_hours == 0.0:
        vetoed = True
        veto_reason = "当前正在降雨，场地湿滑不适合打球"

    if penalty > 0 and not vetoed:
        desc += f"（受前期降雨影响，当前场地湿滑指数：{wetness:.2f}）"
    elif s >= 90 and penalty == 0:
        desc = "预报显示无降雨，场地干爽无积水"

    return s, desc, penalty, vetoed, veto_reason


def _score_thermal(
    temperature_c: float | None, humidity_pct: float | None
) -> tuple[int, str, float | None]:
    """Thermal comfort sub-score (0-100). Returns (score, desc, apparent_temp)."""
    if temperature_c is None:
        return 75, "温度数据暂缺", None

    rh = humidity_pct if humidity_pct else 60.0
    apparent = heat_index_celsius(temperature_c, rh)

    if apparent < 5:
        s = 20
    elif apparent < 10:
        s = 45
    elif apparent < 15:
        s = 70
    elif apparent < 18:
        s = 85
    elif apparent <= 26:
        s = 100
    elif apparent <= 30:
        s = 85
    elif apparent <= 33:
        s = 65
    elif apparent <= 36:
        s = 45
    elif apparent <= 39:
        s = 28
    elif apparent <= 42:
        s = 12
    else:
        s = 0

    # Human-readable description
    apparent_r = round(apparent)
    if s >= 90:
        desc = f"体感{apparent_r}°C，温度舒适，非常适合运动"
    elif s >= 70:
        if apparent < 18:
            desc = f"体感{apparent_r}°C，天气偏凉，建议适当热身"
        else:
            desc = f"体感{apparent_r}°C，温度适中，注意补水"
    elif s >= 50:
        desc = f"体感{apparent_r}°C，有点热，建议多喝水多休息"
    elif s >= 25:
        desc = f"体感{apparent_r}°C，较热，建议缩短运动时间"
    else:
        desc = f"体感{apparent_r}°C，高温风险，不建议剧烈运动"
    return s, desc, apparent


def _score_wind(wind_speed_mps: float | None) -> tuple[int, str]:
    """Wind sub-score (0-100)."""
    if wind_speed_mps is None:
        return 85, "风速数据暂缺"

    ws = wind_speed_mps
    if ws <= 2:
        s = 100
    elif ws <= 4:
        s = 92
    elif ws <= 6:
        s = 78
    elif ws <= 8:
        s = 60
    elif ws <= 11:
        s = 38
    elif ws <= 14:
        s = 15
    else:
        s = 0

    # Human-readable description
    if s >= 90:
        desc = f"微风{ws:.0f}m/s，对打球基本无影响"
    elif s >= 70:
        desc = f"风速{ws:.0f}m/s，略影响抛球"
    elif s >= 50:
        desc = f"风速{ws:.0f}m/s，明显影响高球和发球"
    elif s >= 25:
        desc = f"风速{ws:.0f}m/s，严重影响球路控制"
    else:
        desc = f"风速{ws:.0f}m/s，风力过大不适合打球"
    return s, desc


def _score_aqi(aqi: float | None) -> tuple[int, str]:
    """Air quality sub-score (0-100)."""
    if aqi is None:
        return 85, "空气质量数据暂缺"

    aqi_int = int(aqi)
    if aqi_int <= 50:
        s, desc = 100, f"空气质量优（AQI {aqi_int}），适合运动"
    elif aqi_int <= 100:
        s, desc = 88, f"空气质量良（AQI {aqi_int}），可正常运动"
    elif aqi_int <= 150:
        s, desc = 58, f"轻度污染（AQI {aqi_int}），敏感人群注意"
    elif aqi_int <= 200:
        s, desc = 28, f"中度污染（AQI {aqi_int}），建议减少户外运动"
    else:
        s, desc = 0, f"重度污染（AQI {aqi_int}），不建议户外活动"
    return s, desc


def _score_nowcast(now_risk: int, conflicts: list[str] | None) -> tuple[int, str]:
    """Nowcast stability sub-score (0-100). Derived from risk engine's now_risk."""
    base = max(0, 100 - now_risk)
    conflict_count = len(conflicts) if conflicts else 0
    if conflict_count >= 2:
        base = int(base * 0.80)
    elif conflict_count == 1:
        base = int(base * 0.90)
    s = min(100, max(0, base))

    # Human-readable description
    if s >= 85:
        desc = "短临数据稳定，当前降雨风险很低"
    elif s >= 65:
        desc = "短临数据基本稳定，小幅波动"
    elif s >= 40:
        desc = "短临数据有波动，需关注变化"
    else:
        desc = "短临数据不稳定，降雨风险较高"
    return s, desc


# Default weights (imported from config)
_BASE_WEIGHTS = PLAYABILITY_BASE_WEIGHTS


def _dynamic_weights(
    apparent_temp: float | None, rain_penalty: int = 0
) -> dict[str, float]:
    """Adjust weights dynamically based on extreme conditions.

    When it's very hot, thermal comfort becomes more important.
    When the court is very wet (high rain penalty), rain/wetness becomes the dominant factor.
    """
    w = dict(_BASE_WEIGHTS)

    if rain_penalty > 40:
        # Severe wetness, shift 20% weight to rain/wetness factor
        w["rain"] += 0.20
        w["thermal"] -= 0.05
        w["wind"] -= 0.05
        w["aqi"] -= 0.05
        w["nowcast"] -= 0.05
    elif rain_penalty > 0:
        # Mild wetness, shift 10%
        w["rain"] += 0.10
        w["thermal"] -= 0.03
        w["wind"] -= 0.03
        w["aqi"] -= 0.02
        w["nowcast"] -= 0.02

    if apparent_temp is not None and apparent_temp > 35:
        # Increase thermal weight
        extra = 0.10
        w["thermal"] += extra
        w["aqi"] -= 0.03
        w["nowcast"] -= 0.03
        w["wind"] -= 0.02
        w["rain"] -= 0.02

    # Ensure no negative weights just in case
    for k in w:
        w[k] = max(0.01, w[k])

    return w


# Grade table (imported from config)
_GRADE_TABLE = PLAYABILITY_GRADE_TABLE


def _grade(score: int) -> tuple[str, str]:
    """Map numeric score to (grade_cn, grade_en)."""
    for threshold, cn, en in _GRADE_TABLE:
        if score >= threshold:
            return cn, en
    return "不宜", "conditions_unsuitable"


def compute_playability(
    rain_probability: dict[str, float],
    risk_scores: dict[str, Any],
    grid_realtime: dict[str, Any],
    qpf_has_rain: bool,
    rain_flag: int,
    current_stats: dict[str, float],
) -> dict[str, Any]:
    """Compute multi-horizon playability scores with breakdown.

    Returns a structure with scores for now/30min/60min/120min,
    each containing a total score, grade, veto status, and per-factor breakdown
    with human-readable descriptions.
    """
    temperature = grid_realtime.get("temperature")
    humidity = grid_realtime.get("humidity_pct")
    wind_speed = grid_realtime.get("wind_speed_mps")
    weather_state = grid_realtime.get("weather_state", "")
    aqi = grid_realtime.get("aqi")

    max_dbz = current_stats.get("max_dbz", 0)
    playable_cov = current_stats.get("playable_coverage", 0)

    # Compute apparent temperature once
    thermal_score, thermal_desc, apparent_temp = _score_thermal(temperature, humidity)

    # Check veto (shared across horizons — based on current conditions)
    vetoed, veto_reason = _check_veto(
        weather_state=weather_state,
        qpf_has_rain=qpf_has_rain,
        rain_flag=rain_flag,
        max_dbz=max_dbz,
        playable_coverage=playable_cov,
        apparent_temp=apparent_temp,
        wind_speed=wind_speed,
        aqi=aqi,
    )

    # Dynamic weights base (without wetness penalty yet)
    base_weights = _dynamic_weights(apparent_temp, 0)

    # Factors that don't change per horizon
    wind_score, wind_desc = _score_wind(wind_speed)
    aqi_score, aqi_desc = _score_aqi(aqi)
    conflicts = risk_scores.get("conflicts", [])

    horizons = {
        "now": {
            "rain_prob": rain_probability.get("30min", 0),
            "risk_key": "now_risk",
            "label": "当前",
            "hours": 0.0,
        },
        "30min": {
            "rain_prob": rain_probability.get("30min", 0),
            "risk_key": "risk_30",
            "label": "30分钟",
            "hours": 0.5,
        },
        "60min": {
            "rain_prob": rain_probability.get("60min", 0),
            "risk_key": "risk_60",
            "label": "60分钟",
            "hours": 1.0,
        },
        "120min": {
            "rain_prob": rain_probability.get("120min", 0),
            "risk_key": "risk_120",
            "label": "120分钟",
            "hours": 2.0,
        },
    }

    results: dict[str, Any] = {}

    # We need historical rain for the evaporation model
    rain_1h = grid_realtime.get("rain_1h_mm", 0) or 0.0
    rain_5m = grid_realtime.get("rain_5m_mm", 0) or 0.0

    for horizon, cfg in horizons.items():
        # Evaluate rain and evaporation per horizon
        rain_score, rain_desc, rain_penalty, rain_vetoed, rain_veto_reason = (
            _score_rain(
                cfg["rain_prob"],
                qpf_has_rain,
                rain_flag,
                rain_1h_mm=rain_1h,
                rain_5m_mm=rain_5m,
                weather_state=weather_state,
                temp=temperature,
                humidity=humidity,
                wind_speed=wind_speed,
                horizon_hours=cfg["hours"],
            )
        )

        # Check global environmental vetos
        is_vetoed = vetoed or rain_vetoed
        v_reason = rain_veto_reason if rain_vetoed else veto_reason

        if is_vetoed:
            results[horizon] = {
                "score": 0,
                "grade": "不可打",
                "grade_en": "vetoed",
                "vetoed": True,
                "veto_reason": v_reason,
                "breakdown": {},
            }
            continue

        # Adjust weights dynamically based on the penalty for THIS horizon
        weights = _dynamic_weights(apparent_temp, rain_penalty)

        now_risk = risk_scores.get(cfg["risk_key"], 0)
        nowcast_score, nowcast_desc = _score_nowcast(now_risk, conflicts)

        sub = {
            "rain": rain_score,
            "thermal": thermal_score,
            "nowcast": nowcast_score,
            "wind": wind_score,
            "aqi": aqi_score,
        }

        # Normalize weights to exactly 1.0 sum (floating point drift handling)
        w_sum = sum(weights.values())
        norm_weights = {k: v / w_sum for k, v in weights.items()}

        total = sum(sub[k] * norm_weights[k] for k in norm_weights)
        total = int(round(max(0, min(100, total))))
        grade_cn, grade_en = _grade(total)

        results[horizon] = {
            "score": total,
            "grade": grade_cn,
            "grade_en": grade_en,
            "vetoed": False,
            "veto_reason": None,
            "breakdown": {
                "rain": {
                    "score": rain_score,
                    "weight": round(norm_weights["rain"], 2),
                    "label": "降水与场地",
                    "icon": "cloud-rain",
                    "desc": rain_desc,
                },
                "thermal": {
                    "score": thermal_score,
                    "weight": round(norm_weights["thermal"], 2),
                    "label": "体感温度",
                    "icon": "thermometer",
                    "desc": thermal_desc,
                },
                "nowcast": {
                    "score": nowcast_score,
                    "weight": round(norm_weights["nowcast"], 2),
                    "label": "短临稳定",
                    "icon": "radar",
                    "desc": nowcast_desc,
                },
                "wind": {
                    "score": wind_score,
                    "weight": round(norm_weights["wind"], 2),
                    "label": "风力",
                    "icon": "wind",
                    "desc": wind_desc,
                },
                "aqi": {
                    "score": aqi_score,
                    "weight": round(norm_weights["aqi"], 2),
                    "label": "空气质量",
                    "icon": "leaf",
                    "desc": aqi_desc,
                },
            },
        }

    return results
