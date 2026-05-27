"""预约决策引擎。

基于预报时段（lead time）生成网球场预约决策建议。

三个决策时段：
    0-2h（马上开打）：直接使用短临风险结论，QPF + 雷达全权重
    2-6h（几小时后）：雷达权重降低，逐小时预报主导
    6h+（较远期）：仅供背景参考，输出复查时间建议

决策枚举：
    keep_booking     — 保留预约，可以出发
    keep_but_recheck — 保留预约，赛前复查
    wait_and_see     — 建议观望
    suggest_cancel   — 建议取消或改期
    background_only  — 仅供背景参考
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from config import RAIN_KEYWORDS


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
    """生成预约决策。

    Args:
        risk_scores: 风险引擎输出（含 conclusion / now_risk 等）。
        lead_time_hours: 距开场时间（小时）。
        target_time_str: 目标开场时间字符串（HH:MM）。
        play_duration_minutes: 预计打球时长（分钟）。
        hourly_forecast: 逐小时预报列表。
        seven_day_forecast: 7 天预报列表。
        qpf6min_all_zero: QPF 是否全程无雨。
        rain_flag: 第二官方源降雨标志。
        grid_realtime: 气象站实况数据。
        now: 当前时间（可注入，便于测试）。

    Returns:
        预约决策字典，包含 decision / decision_cn / reason / caveat 等字段。
    """
    if now is None:
        now = datetime.now()

    # ---- 确定时段分段 ----
    if lead_time_hours <= 2:
        band = "0-2h"
    elif lead_time_hours <= 6:
        band = "2-6h"
    else:
        band = "6h+"

    # ---- 解析目标小时（用于匹配逐时预报） ----
    try:
        target_hour = int(target_time_str.split(":")[0]) if ":" in target_time_str else -1
    except (ValueError, IndexError):
        target_hour = -1

    # ---- 扫描打球窗口内的降雨预报 ----
    play_hours = max(1, play_duration_minutes // 60)
    window_rain_count = 0        # 窗口内有雨的小时数
    pre_window_rain = False      # 开场前 2 小时是否有雨（场地可能提前变湿）

    for h in hourly_forecast:
        h_time = h.get("time", "")
        h_weather = h.get("weather", "")
        try:
            h_hour = int(h_time.split(":")[0]) if ":" in h_time else -1
        except (ValueError, IndexError):
            h_hour = -1

        if h_hour < 0 or target_hour < 0:
            continue

        # 处理跨午夜比较（如目标 22:00，预报 01:00）
        h_norm = h_hour if h_hour >= 12 or target_hour < 12 else h_hour + 24
        t_norm = target_hour if target_hour >= 12 or h_hour < 12 else target_hour + 24

        # 开场前 2 小时窗口检查
        if t_norm - 2 <= h_norm < t_norm:
            if any(kw in h_weather for kw in RAIN_KEYWORDS):
                pre_window_rain = True

        # 打球窗口检查
        if t_norm <= h_norm < t_norm + play_hours:
            if any(kw in h_weather for kw in RAIN_KEYWORDS):
                window_rain_count += 1

    # ---- 检查当天是否有降雨背景 ----
    today_rain_bg = False
    if seven_day_forecast:
        for d in seven_day_forecast[:2]:
            label = d.get("label", "")
            if "今天" in label or "今日" in label:
                for field in ["weather_day", "weather_night"]:
                    if any(kw in d.get(field, "") for kw in RAIN_KEYWORDS):
                        today_rain_bg = True

    reasons = []
    caveats = []

    # ---- 检查当前是否正在下雨 ----
    grid_weather = grid_realtime.get("weather_state", "")
    is_raining_now = (
        any(kw in grid_weather for kw in RAIN_KEYWORDS)
        or grid_realtime.get("rain_5m_mm", 0) > 0
        or grid_realtime.get("rain_1h_mm", 0) > 0
    )

    # ═══════════════════════════════════════════════════════════════════════
    # 分段决策逻辑
    # ═══════════════════════════════════════════════════════════════════════

    if band == "0-2h":
        # 直接使用风险引擎结论
        conclusion = risk_scores.get("conclusion", "playable")
        decision_map = {
            "playable": ("keep_booking", "保留预约，可以出发"),
            "cautious": ("keep_but_recheck", "保留预约，谨慎出发"),
            "not_recommended": ("suggest_cancel", "建议取消或改期"),
            "seek_shelter": ("suggest_cancel", "建议取消"),
        }
        decision, decision_cn = decision_map.get(conclusion, ("keep_booking", "保留预约"))

        if is_raining_now:
            decision = "suggest_cancel"
            decision_cn = "建议取消或改期"
            reasons.append(f"当前实况为「{grid_weather}」，场地可能已经湿滑")

        if qpf6min_all_zero and rain_flag == 0:
            if is_raining_now:
                reasons.append("官方短临显示未来2小时无雨，但当前实况已有降水，存在矛盾")
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

        # 复查时间：开场前 15 分钟
        check_at = now + timedelta(minutes=max(15, lead_time_hours * 60 - 15))
        check_again = check_at.strftime("%H:%M")

    elif band == "2-6h":
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

        # 复查时间：目标时间前 45 分钟，但不早于当前时间后 30 分钟
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

        # 安排两次复查
        recheck1 = now + timedelta(hours=max(1, lead_time_hours - 2.5))
        recheck2 = now + timedelta(hours=max(2, lead_time_hours - 0.75))
        check_again = f"{recheck1.strftime('%H:%M')} 和 {recheck2.strftime('%H:%M')}"
        caveats.append("超过6小时的预判以天气背景为主，不宜作为最终取消依据")

    # ---- 计算打球窗口字符串 ----
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
