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


# --- Thresholds (FROZEN for calibration period) ---
DBZ_NONE = 15
DBZ_WEAK = 25
DBZ_MODERATE = 35
DBZ_STRONG = 45
RH_LOW = 70.0
RH_MID = 85.0
RH_HIGH = 90.0

TREND_WEIGHTS_6 = np.array([0.5, 0.7, 0.9, 1.1, 1.3, 1.5])
TREND_WEIGHTS_3 = np.array([0.7, 1.0, 1.3])

RAIN_KEYWORDS = {"小雨", "中雨", "大雨", "暴雨", "雷阵雨", "阵雨", "雷雨"}
MIN_UPSTREAM_COVERAGE_25 = 0.02  # 2% minimum for meaningful upstream echo


# ---- 1. Frame Quality Control ----

def frame_quality(dbz: np.ndarray, mask: np.ndarray,
                  prev_dbz: np.ndarray | None = None) -> float:
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


def compute_trends(frames_dbz: list[np.ndarray], mask: np.ndarray,
                   quality_scores: list[float]) -> dict[str, Any]:
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

def detect_upstream_echo(latest_dbz: np.ndarray, mask: np.ndarray,
                         dx: float, dy: float,
                         steps: int = 5) -> dict[str, Any]:
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
        upstream_level = "trace"       # Isolated pixel, not actionable
    elif cov_25 < 0.05:
        upstream_level = "weak"        # Small patch, monitor only
    else:
        upstream_level = "organized"   # Significant echo band

    has_upstream = (
        max_up >= DBZ_WEAK and cov_25 >= MIN_UPSTREAM_COVERAGE_25
    )

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
) -> dict[str, Any]:
    """Four-layer fusion engine producing risk scores + conclusion."""

    humidity = grid_realtime.get("humidity_pct", 75.0) or 75.0
    weather = grid_realtime.get("weather_state", "") or ""
    max_dbz = current_stats.get("max_dbz", 0)
    cov25 = current_stats.get("playable_coverage", 0)

    # ---- Layer 1: Official QPF base risk ----
    if qpf6min_all_zero and rain_flag == 0:
        qpf_base = 5  # Both agree: no rain
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
        radar_mod += 5   # Possible re-development

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

    if any(kw in weather for kw in RAIN_KEYWORDS):
        surface_mod += 10

    # ---- Layer 4: Background ----
    bg_mod = 0
    near_hours = hourly_forecast[:6] if hourly_forecast else []
    rain_hours = sum(1 for h in near_hours
                     if any(kw in h.get("weather", "") for kw in RAIN_KEYWORDS))
    if rain_hours >= 3:
        bg_mod += 10
    elif rain_hours >= 1:
        bg_mod += 5

    # ---- Compute per-horizon risk ----
    # now_risk: current frame + trend_3 dominant
    now_risk = _clamp(qpf_base * 0.4 + radar_mod * 1.0 + surface_mod * 1.0 + bg_mod * 0.3)

    # risk_30: QPF highest, radar+trend_3 secondary
    r30_radar = radar_mod + rain_probability.get("30min", 0) * 30
    risk_30 = _clamp(qpf_base * 0.6 + r30_radar * 0.8 + surface_mod * 0.6 + bg_mod * 0.3)

    # risk_60: QPF highest, trend_6 more weight
    r60_radar = radar_mod + rain_probability.get("60min", 0) * 25
    risk_60 = _clamp(qpf_base * 0.7 + r60_radar * 0.6 + surface_mod * 0.5 + bg_mod * 0.5)

    # risk_120: QPF dominant, radar only auxiliary
    r120_radar = radar_mod * 0.3 + rain_probability.get("120min", 0) * 15
    risk_120 = _clamp(qpf_base * 0.8 + r120_radar * 0.4 + surface_mod * 0.4 + bg_mod * 0.8)

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
    if any(kw in weather for kw in RAIN_KEYWORDS) and max_dbz < DBZ_NONE and qpf6min_all_zero:
        conflicts.append("weather_state_rain_but_no_radar_no_qpf")

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
            "surface_mod": surface_mod,
            "background_mod": bg_mod,
        },
    }


# ---- 5. Calibration Logging ----

def save_calibration_log(report: dict[str, Any], risk_scores: dict[str, Any],
                         log_path: str = "output/calibration_log.jsonl") -> None:
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
        target_hour = int(target_time_str.split(":")[0]) if ":" in target_time_str else -1
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
        decision, decision_cn = decision_map.get(conclusion, ("keep_booking", "保留预约"))

        if qpf6min_all_zero and rain_flag == 0:
            reasons.append("官方短临一致支持未来2小时无雨")
        if risk_scores.get("now_risk", 0) < 25:
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

        caveats.append(f"若{check_again}后QPF出现非零降雨或上游出现≥25dBZ回波，应重新评估")

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

