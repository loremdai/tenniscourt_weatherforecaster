"""可打性评分系统。

为网球场提供多时段（当前/30min/60min/120min）的综合可打性评分，
每个时段输出 0-100 的总分、等级、否决状态，以及五个维度的子评分明细。

五维评分体系：
    1. 降雨与场地（rain）    — 融合雷达概率 + QPF + rainFlag + 场地湿度衰减模型
    2. 体感温度（thermal）   — Rothfusz 体感温度回归 → 舒适度映射
    3. 短临稳定（nowcast）   — 基于风险引擎的 now_risk + 冲突数量
    4. 风力（wind）          — 风速对网球运动的影响分级
    5. 空气质量（aqi）       — AQI 指数分级

特殊机制：
    - 否决层（veto）：极端条件下直接判定"不可打"（score=0）
    - 动态权重：高温时体感权重上升，场地湿滑时降雨权重上升
    - 场地湿度衰减模型：半物理指数衰减，考虑温度/风速/湿度对蒸发的影响
"""

from __future__ import annotations

import math
from typing import Any

from config import (
    DBZ_MODERATE,
    RAIN_KEYWORDS,
    PLAYABILITY_BASE_WEIGHTS,
    PLAYABILITY_GRADE_TABLE,
)


# ═══════════════════════════════════════════════════════════════════════════════
# 体感温度
# ═══════════════════════════════════════════════════════════════════════════════


def heat_index_celsius(t_c: float, rh: float) -> float:
    """Rothfusz 回归公式计算体感温度（Heat Index）。

    仅当气温 ≥ 27°C 且相对湿度 ≥ 40% 时适用。
    其余条件下直接返回干球温度。

    公式来源：美国国家气象局 (NWS) Rothfusz 1990 回归方程。
    内部先转华氏度计算，再转回摄氏度。
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


# ═══════════════════════════════════════════════════════════════════════════════
# 否决层（Veto）
# ═══════════════════════════════════════════════════════════════════════════════


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
    """否决层 — 极端条件下的硬约束，触发时可打性直接归零。

    否决条件（任一满足即触发）：
        V2: 三源一致降雨 + 强雷达回波（QPF+rainFlag+dBZ≥35+覆盖率≥5%）
        V3: 体感温度 ≥ 42°C（中暑风险）
        V4: 风速 ≥ 14 m/s ≈ 50 km/h（无法打球）
        V5: AQI ≥ 201（重度污染）

    注意：V1（当前降雨否决）已改为按时段动态处理，
    不再全局否决所有未来时段。

    Returns:
        (is_vetoed, reason_cn) 元组。
    """
    # V2: 多源一致 + 强回波
    if (
        qpf_has_rain
        and rain_flag == 1
        and max_dbz >= DBZ_MODERATE
        and playable_coverage >= 0.05
    ):
        return True, "多个气象源一致预报短时有较强降雨"

    # V3: 极端高温
    if apparent_temp is not None and apparent_temp >= 42:
        return True, f"体感温度过高（{apparent_temp:.0f}°C），存在中暑风险"

    # V4: 大风
    if wind_speed is not None and wind_speed >= 14:
        return True, f"风力过大（{wind_speed:.1f}m/s），无法正常打球"

    # V5: 重度污染
    if aqi is not None and aqi >= 201:
        return True, f"空气质量严重不佳（AQI {int(aqi)}），不建议户外运动"

    return False, None


# ═══════════════════════════════════════════════════════════════════════════════
# 场地湿度衰减模型
# ═══════════════════════════════════════════════════════════════════════════════


def estimate_court_wetness(
    rain_1h_mm: float,
    rain_5m_mm: float,
    is_raining_now: bool,
    temp: float | None,
    humidity: float | None,
    wind_speed: float | None,
    horizon_hours: float,
) -> float:
    """半物理指数衰减模型，估算未来某时刻的场地湿滑程度 (0.0~1.0)。

    模型原理：
        1. 用近期降雨量估算当前湿度初始值（wetness_now）
        2. 根据温度、风速、空气湿度计算蒸发干燥速率
        3. 用指数衰减 ``wetness_now * exp(-rate * hours)`` 预测未来湿度

    干燥速率影响因素：
        - 温度 > 20°C 加速蒸发，< 20°C 减缓
        - 风速越大蒸发越快（上限 8 m/s）
        - 空气湿度 > 60% 时抑制蒸发

    Returns:
        0.0（完全干燥）到 1.0（严重积水）的湿滑指数。
    """
    # 当前湿度初始值：融合 5 分钟雨量、1 小时雨量和实时降雨状态
    rain_input = (
        0.35 * min(1.0, rain_5m_mm / 1.0)
        + 0.45 * min(1.0, rain_1h_mm / 5.0)
        + 0.20 * float(is_raining_now)
    )
    wetness_now = min(1.0, rain_input)

    # 缺省值处理
    t = temp if temp is not None else 20.0
    rh = humidity if humidity is not None else 75.0
    w = wind_speed if wind_speed is not None else 0.0

    # 各因子对蒸发速率的贡献
    temp_factor = 1.0 + 0.03 * max(0.0, t - 20.0) - 0.025 * max(0.0, 20.0 - t)
    wind_factor = 1.0 + 0.12 * min(max(w, 0.0), 8.0)
    humidity_factor = 1.0 - 0.006 * max(0.0, rh - 60.0)

    # 综合干燥速率，限制在合理范围内
    drying_rate = 0.85 * temp_factor * wind_factor * humidity_factor
    drying_rate = max(0.15, min(1.8, drying_rate))

    # 指数衰减
    wetness = wetness_now * math.exp(-drying_rate * horizon_hours)
    return max(0.0, min(1.0, wetness))


# ═══════════════════════════════════════════════════════════════════════════════
# 五维子评分函数
# ═══════════════════════════════════════════════════════════════════════════════


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
    """降雨与场地子评分 (0-100)。

    融合三个独立降雨信号（雷达概率 50% + QPF 30% + rainFlag 20%）
    计算基础分，再叠加场地湿度衰减模型的惩罚。

    Returns:
        (score, desc, penalty, vetoed, veto_reason) 五元组。
        penalty 是湿度惩罚分值，供动态权重调整使用。
    """
    # 融合三个降雨信号为单一指标
    signal = (
        0.50 * rain_prob
        + 0.30 * (1.0 if qpf_has_rain else 0.0)
        + 0.20 * (1.0 if rain_flag == 1 else 0.0)
    )

    # 信号强度 → 基础分映射
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

    # 生成面向用户的文字描述
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

    # 场地湿度衰减模型 — 用 config 中统一的关键词列表（修复原 shadowing bug）
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

    # 湿度惩罚：最大扣 80 分
    penalty = int(round(80 * wetness))
    s = max(0, s - penalty)

    # 湿度否决判定
    vetoed = False
    veto_reason = None
    if wetness >= 0.75:
        vetoed = True
        veto_reason = "场地严重湿滑或积水，极易滑倒"
    elif is_raining_now and horizon_hours == 0.0:
        vetoed = True
        veto_reason = "当前正在降雨，场地湿滑不适合打球"

    # 补充描述
    if penalty > 0 and not vetoed:
        desc += f"（受前期降雨影响，当前场地湿滑指数：{wetness:.2f}）"
    elif s >= 90 and penalty == 0:
        desc = "预报显示无降雨，场地干爽无积水"

    return s, desc, penalty, vetoed, veto_reason


def _score_thermal(
    temperature_c: float | None, humidity_pct: float | None
) -> tuple[int, str, float | None]:
    """体感温度子评分 (0-100)。

    使用 Rothfusz 体感温度，映射到运动舒适度分数。
    18-26°C 为满分区间，向两端递减。

    Returns:
        (score, desc, apparent_temp) 三元组。
    """
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
    """风力子评分 (0-100)。

    基于风速对网球运动的实际影响分级：
        ≤2 m/s: 100（微风无影响）
        ≤4: 92 | ≤6: 78 | ≤8: 60 | ≤11: 38 | ≤14: 15 | >14: 0
    """
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
    """空气质量子评分 (0-100)，基于国标 AQI 分级。"""
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
    """短临稳定性子评分 (0-100)。

    基于风险引擎的 now_risk（0-100）反转为稳定性分数，
    再根据数据冲突数量做折扣（冲突越多，置信度越低）。
    """
    base = max(0, 100 - now_risk)
    conflict_count = len(conflicts) if conflicts else 0
    if conflict_count >= 2:
        base = int(base * 0.80)
    elif conflict_count == 1:
        base = int(base * 0.90)
    s = min(100, max(0, base))

    if s >= 85:
        desc = "短临数据稳定，当前降雨风险很低"
    elif s >= 65:
        desc = "短临数据基本稳定，小幅波动"
    elif s >= 40:
        desc = "短临数据有波动，需关注变化"
    else:
        desc = "短临数据不稳定，降雨风险较高"
    return s, desc


# ═══════════════════════════════════════════════════════════════════════════════
# 动态权重与等级映射
# ═══════════════════════════════════════════════════════════════════════════════

# 从 config 导入的基准权重和等级表
_BASE_WEIGHTS = PLAYABILITY_BASE_WEIGHTS
_GRADE_TABLE = PLAYABILITY_GRADE_TABLE


def _dynamic_weights(
    apparent_temp: float | None, rain_penalty: int = 0
) -> dict[str, float]:
    """根据极端条件动态调整五维权重。

    调整规则：
        - 场地严重湿滑（penalty > 40）：rain 权重 +20%，其余四项各减 5%
        - 场地轻度湿滑（penalty > 0）：rain 权重 +10%
        - 高温（体感 > 35°C）：thermal 权重 +10%

    所有权重最终保证 ≥ 0.01（防止除零）。
    """
    w = dict(_BASE_WEIGHTS)

    if rain_penalty > 40:
        w["rain"] += 0.20
        w["thermal"] -= 0.05
        w["wind"] -= 0.05
        w["aqi"] -= 0.05
        w["nowcast"] -= 0.05
    elif rain_penalty > 0:
        w["rain"] += 0.10
        w["thermal"] -= 0.03
        w["wind"] -= 0.03
        w["aqi"] -= 0.02
        w["nowcast"] -= 0.02

    if apparent_temp is not None and apparent_temp > 35:
        extra = 0.10
        w["thermal"] += extra
        w["aqi"] -= 0.03
        w["nowcast"] -= 0.03
        w["wind"] -= 0.02
        w["rain"] -= 0.02

    for k in w:
        w[k] = max(0.01, w[k])

    return w


def _grade(score: int) -> tuple[str, str]:
    """将数值分数映射为中英文等级。"""
    for threshold, cn, en in _GRADE_TABLE:
        if score >= threshold:
            return cn, en
    return "不宜", "conditions_unsuitable"


# ═══════════════════════════════════════════════════════════════════════════════
# 主函数：多时段可打性评分
# ═══════════════════════════════════════════════════════════════════════════════


def compute_playability(
    rain_probability: dict[str, float],
    risk_scores: dict[str, Any],
    grid_realtime: dict[str, Any],
    qpf_has_rain: bool,
    rain_flag: int,
    current_stats: dict[str, float],
) -> dict[str, Any]:
    """计算多时段可打性综合评分。

    对 now / 30min / 60min / 120min 四个时段分别计算：
        1. 五维子评分（降雨、体感、短临、风力、空气）
        2. 否决检查（环境否决 + 降雨/湿度否决）
        3. 动态权重调整
        4. 加权总分 → 等级映射

    Args:
        rain_probability: 各时段雷达降雨概率。
        risk_scores: 风险引擎输出（含 now_risk 等）。
        grid_realtime: 气象站实况数据。
        qpf_has_rain: QPF 是否有雨。
        rain_flag: 第二官方源降雨标志。
        current_stats: 当前雷达统计（max_dbz, playable_coverage 等）。

    Returns:
        四个时段的评分结构，含 score/grade/vetoed/breakdown 等字段。
    """
    temperature = grid_realtime.get("temperature")
    humidity = grid_realtime.get("humidity_pct")
    wind_speed = grid_realtime.get("wind_speed_mps")
    weather_state = grid_realtime.get("weather_state", "")
    aqi = grid_realtime.get("aqi")
    max_dbz = current_stats.get("max_dbz", 0)
    playable_cov = current_stats.get("playable_coverage", 0)

    # 体感温度（所有时段共享）
    thermal_score, thermal_desc, apparent_temp = _score_thermal(temperature, humidity)

    # 环境否决检查（所有时段共享）
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

    # 不随时段变化的子评分
    wind_score, wind_desc = _score_wind(wind_speed)
    aqi_score, aqi_desc = _score_aqi(aqi)
    conflicts = risk_scores.get("conflicts", [])

    # 四个评估时段配置
    horizons = {
        "now": {"rain_prob": rain_probability.get("30min", 0), "risk_key": "now_risk", "hours": 0.0},
        "30min": {"rain_prob": rain_probability.get("30min", 0), "risk_key": "risk_30", "hours": 0.5},
        "60min": {"rain_prob": rain_probability.get("60min", 0), "risk_key": "risk_60", "hours": 1.0},
        "120min": {"rain_prob": rain_probability.get("120min", 0), "risk_key": "risk_120", "hours": 2.0},
    }

    results: dict[str, Any] = {}
    rain_1h = grid_realtime.get("rain_1h_mm", 0) or 0.0
    rain_5m = grid_realtime.get("rain_5m_mm", 0) or 0.0

    for horizon, cfg in horizons.items():
        # 降雨子评分（每个时段独立计算，因为湿度衰减不同）
        rain_score, rain_desc, rain_penalty, rain_vetoed, rain_veto_reason = (
            _score_rain(
                cfg["rain_prob"], qpf_has_rain, rain_flag,
                rain_1h_mm=rain_1h, rain_5m_mm=rain_5m,
                weather_state=weather_state, temp=temperature,
                humidity=humidity, wind_speed=wind_speed,
                horizon_hours=cfg["hours"],
            )
        )

        # 合并环境否决 + 降雨否决
        is_vetoed = vetoed or rain_vetoed
        v_reason = rain_veto_reason if rain_vetoed else veto_reason

        if is_vetoed:
            results[horizon] = {
                "score": 0, "grade": "不可打", "grade_en": "vetoed",
                "vetoed": True, "veto_reason": v_reason, "breakdown": {},
            }
            continue

        # 动态权重（根据本时段的湿度惩罚调整）
        weights = _dynamic_weights(apparent_temp, rain_penalty)

        now_risk = risk_scores.get(cfg["risk_key"], 0)
        nowcast_score, nowcast_desc = _score_nowcast(now_risk, conflicts)

        sub = {"rain": rain_score, "thermal": thermal_score, "nowcast": nowcast_score, "wind": wind_score, "aqi": aqi_score}

        # 归一化权重（处理浮点漂移）
        w_sum = sum(weights.values())
        norm_weights = {k: v / w_sum for k, v in weights.items()}

        total = sum(sub[k] * norm_weights[k] for k in norm_weights)
        total = int(round(max(0, min(100, total))))
        grade_cn, grade_en = _grade(total)

        results[horizon] = {
            "score": total, "grade": grade_cn, "grade_en": grade_en,
            "vetoed": False, "veto_reason": None,
            "breakdown": {
                "rain": {"score": rain_score, "weight": round(norm_weights["rain"], 2), "label": "降水与场地", "icon": "cloud-rain", "desc": rain_desc},
                "thermal": {"score": thermal_score, "weight": round(norm_weights["thermal"], 2), "label": "体感温度", "icon": "thermometer", "desc": thermal_desc},
                "nowcast": {"score": nowcast_score, "weight": round(norm_weights["nowcast"], 2), "label": "短临稳定", "icon": "radar", "desc": nowcast_desc},
                "wind": {"score": wind_score, "weight": round(norm_weights["wind"], 2), "label": "风力", "icon": "wind", "desc": wind_desc},
                "aqi": {"score": aqi_score, "weight": round(norm_weights["aqi"], 2), "label": "空气质量", "icon": "leaf", "desc": aqi_desc},
            },
        }

    return results
