"""CAPPI 雷达短临预报核心模块。

本模块是雷达数据链路的协调层，负责：
    1. 定义公共数据结构（Bounds、Frame）
    2. 解析 API 响应中的雷达元数据（帧列表、地理范围、时间戳）
    3. 提取气象站实况和预报数据
    4. 组装完整的预报报告（build_report）

具体的网络 I/O 操作由 data_fetcher.py 负责，
具体的图像处理与计算由 radar_processor.py 负责。
本模块通过 re-export 将它们的公共接口统一暴露，
下游模块可以继续使用 ``from nowcast import XXX`` 而无需改动。
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from config import (
    COURT,
    RADIUS_KM,
    RADAR_ECHO_THRESHOLD_DBZ,
    PLAYABLE_RAIN_THRESHOLD_DBZ,
    HORIZONS,
    SOURCE_TZ,
)
from risk_engine import (
    frame_quality,
    compute_trends,
    detect_upstream_echo,
    compute_risk_scores,
    save_calibration_log,
    compute_playability,
)

# ═══════════════════════════════════════════════════════════════════════════════
# Re-export：将拆分后的子模块接口统一暴露
# ═══════════════════════════════════════════════════════════════════════════════
#
# 下游模块（pipeline.py、llm_service.py 等）仍可通过
#   from nowcast import fetch_weather_data, court_mask, ...
# 的方式导入，无需感知内部拆分。

from data_fetcher import (  # noqa: F401
    fetch_weather_data,
    fetch_grid_weather,
    load_response,
    download_frame,
    cleanup_cache,
)
from radar_processor import (  # noqa: F401
    image_to_dbz,
    lon_lat_to_pixel,
    pixel_grids,
    haversine_km,
    court_mask,
    estimate_motion,
    translate_dbz,
    dbz_to_rain_rate,
    summarize_area,
    probability_from_stats,
    confidence_label,
    create_debug_image,
    save_radar_frames,
    create_radar_contact_sheet,
)


# ═══════════════════════════════════════════════════════════════════════════════
# 公共数据结构
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Bounds:
    """CAPPI 雷达图的地理边界（经纬度范围）。

    对应 API 返回的 cappi_bounds 字段，用于像素 ↔ 经纬度的坐标转换。
    """
    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float


@dataclass(frozen=True)
class Frame:
    """一帧 CAPPI 雷达数据。

    包含该帧的时间戳、图片 URL、本地缓存路径、
    反算后的 dBZ 数组和原始 RGBA 图像。
    """
    timestamp: datetime
    url: str
    path: Path
    dbz: np.ndarray
    rgba: Image.Image


# ═══════════════════════════════════════════════════════════════════════════════
# API 响应解析
# ═══════════════════════════════════════════════════════════════════════════════


def first_row(payload: dict[str, Any]) -> dict[str, Any]:
    """从 API 响应中取出第一行数据记录。

    GD121 API 的响应格式为 ``{"rows": [{...}]}``，所有核心数据
    （cappi、cappi_bounds、qpf6min 等）都在 rows[0] 中。

    Args:
        payload: API 响应的完整 JSON 字典。

    Returns:
        rows[0] 字典。

    Raises:
        ValueError: 响应中不包含 rows 或 rows 为空。
    """
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Response does not contain rows[0]")
    if not isinstance(rows[0], dict):
        raise ValueError("rows[0] is not an object")
    return rows[0]


def parse_bounds(row: dict[str, Any]) -> Bounds:
    """解析 API 响应中的 CAPPI 地理边界。

    cappi_bounds 的格式为 ``[[min_lat, min_lon], [max_lat, max_lon]]``，
    可能是 JSON 字符串或已解析的列表。

    Args:
        row: first_row() 返回的数据记录。

    Returns:
        Bounds 对象。

    Raises:
        ValueError: cappi_bounds 缺失或格式异常。
    """
    raw = row.get("cappi_bounds")
    if not raw:
        raise ValueError("Missing cappi_bounds")
    bounds = json.loads(raw) if isinstance(raw, str) else raw
    if (
        not isinstance(bounds, list)
        or len(bounds) != 2
        or not all(isinstance(item, list) and len(item) == 2 for item in bounds)
    ):
        raise ValueError(f"Unexpected cappi_bounds shape: {bounds!r}")
    min_lat, min_lon = map(float, bounds[0])
    max_lat, max_lon = map(float, bounds[1])
    return Bounds(min_lat=min_lat, min_lon=min_lon, max_lat=max_lat, max_lon=max_lon)


def parse_cappi_timestamp(url: str) -> datetime:
    """从 CAPPI 图片 URL 中提取时间戳。

    URL 格式示例：``.../CAPPI_12345_20260527140000.png``
    从中提取 14 位时间字符串并解析为 UTC+8 的 datetime。

    Args:
        url: CAPPI 帧的图片 URL。

    Returns:
        带时区的 datetime 对象。

    Raises:
        ValueError: URL 中未找到预期的时间戳格式。
    """
    match = re.search(r"CAPPI_\d+_(\d{14})\.png", url)
    if not match:
        raise ValueError(f"Cannot parse CAPPI timestamp from URL: {url}")
    return datetime.strptime(match.group(1), "%Y%m%d%H%M%S").replace(tzinfo=SOURCE_TZ)


def collect_cappi(row: dict[str, Any], max_frames: int) -> list[tuple[datetime, str]]:
    """从 API 响应中整理 CAPPI 帧列表。

    提取所有帧的时间戳和 URL，按时间排序后取最近 max_frames 帧。
    URL 中的转义斜杠 (``\\/``) 会被修正。

    Args:
        row: first_row() 返回的数据记录。
        max_frames: 最多保留的帧数。

    Returns:
        (timestamp, url) 元组列表，按时间升序排列。

    Raises:
        ValueError: 响应中不包含 cappi 帧。
    """
    cappi = row.get("cappi")
    if not isinstance(cappi, list) or not cappi:
        raise ValueError("Response does not contain cappi frames")
    frames: list[tuple[datetime, str]] = []
    for item in cappi:
        if isinstance(item, dict) and isinstance(item.get("url"), str):
            url = item["url"].replace("\\/", "/")
            frames.append((parse_cappi_timestamp(url), url))
    frames.sort(key=lambda pair: pair[0])
    return frames[-max_frames:]


# ═══════════════════════════════════════════════════════════════════════════════
# 帧加载
# ═══════════════════════════════════════════════════════════════════════════════


def load_frames(
    entries: list[tuple[datetime, str]], cache_dir: Path, download_timeout: float = 30
) -> list[Frame]:
    """下载并加载 CAPPI 帧，完成 PNG → dBZ 转换。

    协调 data_fetcher（下载）和 radar_processor（图像转换）两层：
        1. 调用 download_frame() 下载/命中缓存
        2. 用 PIL 打开图片
        3. 调用 image_to_dbz() 反算 dBZ 数组
        4. 校验帧数和尺寸一致性

    Args:
        entries: collect_cappi() 返回的 (timestamp, url) 列表。
        cache_dir: 本地缓存目录。
        download_timeout: 每帧下载超时时间。

    Returns:
        Frame 对象列表。

    Raises:
        ValueError: 帧数不足 2（光流至少需要两帧），或帧尺寸不一致。
    """
    frames: list[Frame] = []
    for timestamp, url in entries:
        path = download_frame(url, cache_dir, timeout=download_timeout)
        rgba = Image.open(path).convert("RGBA")
        frames.append(
            Frame(
                timestamp=timestamp,
                url=url,
                path=path,
                dbz=image_to_dbz(rgba),
                rgba=rgba,
            )
        )
    if len(frames) < 2:
        raise ValueError("At least two CAPPI frames are required for optical flow")
    sizes = {frame.dbz.shape for frame in frames}
    if len(sizes) != 1:
        raise ValueError(f"CAPPI frame sizes differ: {sizes}")
    return frames


# ═══════════════════════════════════════════════════════════════════════════════
# QPF 与官方预报
# ═══════════════════════════════════════════════════════════════════════════════


def check_qpf_rain(row: dict[str, Any], steps: int) -> bool:
    """检查官方 QPF 在指定步数内是否有降雨。

    逐条检查 qpf6min 数组中前 steps 条的降雨量 (r 字段)，
    只要有一条 > 0 即返回 True。

    Args:
        row: first_row() 返回的数据记录。
        steps: 检查的步数（每步 6 分钟）。

    Returns:
        True 表示 QPF 预报有降雨。
    """
    qpf = row.get("qpf6min", [])
    if not isinstance(qpf, list):
        return False
    for item in qpf[:steps]:
        if isinstance(item, dict):
            r_str = item.get("r", "0")
            try:
                if float(r_str) > 0:
                    return True
            except (ValueError, TypeError):
                pass
    return False


def official_summary(row: dict[str, Any]) -> dict[str, Any]:
    """提取 API 响应中的官方 QPF 摘要信息。

    包含 QPF 文字摘要、起始时间以及逐 6 分钟降雨预报明细。
    这些信息会直接嵌入最终报告，供 LLM 诊断参考。

    Args:
        row: first_row() 返回的数据记录。

    Returns:
        包含 official_qpf6min_summary / origin_dt / 明细列表的字典。
    """
    qpf6min = row.get("qpf6min") if isinstance(row.get("qpf6min"), list) else []
    return {
        "official_qpf6min_summary": row.get("qpf6min_summary", ""),
        "official_qpf6min_origin_dt": row.get("qpf6min_origin_dt", ""),
        "official_qpf6min": [
            {
                "dt": item.get("dt"),
                "r": item.get("r"),
                "ro": item.get("ro"),
                "s": item.get("s"),
            }
            for item in qpf6min
            if isinstance(item, dict)
        ],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 气象站数据提取
# ═══════════════════════════════════════════════════════════════════════════════


def extract_station_and_forecast_data(
    row: dict[str, Any], grid_data: dict[str, Any] | None
) -> dict[str, Any]:
    """从两个 API 源提取实况天气和预报数据。

    数据融合策略：
        - 实况数据（温湿风、雨量等）来自 API 1 的最近气象站观测
        - 逐时预报和 7 天预报来自 API 2 的格点插值
        - 如果 API 2 失败（grid_data=None），预报字段为空列表

    占位数据过滤：
        - API 有时会返回"占位"记录（温度=0, 天气=晴, 风向=西北风），
          这些是无效数据，会被自动跳过

    Args:
        row: first_row() 返回的数据记录（含 sk_* 气象站字段）。
        grid_data: fetch_grid_weather() 返回的格点数据，或 None。

    Returns:
        融合后的站点实况 + 预报字典。
    """
    # ---- 1. 提取 API 1 的气象站实况 ----
    def _safe_float(val, default=0.0):
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    humidity = _safe_float(row.get("sk_h"))
    temp = _safe_float(row.get("sk_t"))
    wind_speed = _safe_float(row.get("sk_wp"))
    rain_1h = _safe_float(row.get("sk_r1h"))
    rain_5m = _safe_float(row.get("sk_r5m"))

    station_realtime = {
        "source": "station_observed",
        "station_name": row.get("sk_name", ""),
        "distance_m": row.get("sk_to_you_meter", 0),
        "observation_time": row.get("sk_time", ""),
        "temperature": temp,
        "temperature_feels": row.get("sk_t_feel"),
        "humidity_pct": humidity,
        "wind_direction": row.get("sk_V11201Str", row.get("sk_w", "")),
        "wind_power_level": row.get("sk_wp_level", ""),
        "wind_speed_mps": wind_speed,
        "weather_state": row.get("sk_s", ""),
        "aqi": row.get("pm_aqi"),
        "aqi_level": row.get("pm_q", ""),
        "rain_1h_mm": rain_1h,
        "rain_5m_mm": rain_5m,
        "rain_2h_message": "",
        "rain_2h_flag": 0,
        "hourly_forecast": [],
        "seven_day_forecast": [],
    }

    # ---- 2. 融合 API 2 的预报数据 ----
    if grid_data:
        # 未来 2 小时降雨预报
        rain_dto = grid_data.get("nowTwoHourResDto", {})
        station_realtime["rain_2h_message"] = rain_dto.get("message", "")
        station_realtime["rain_2h_flag"] = rain_dto.get("rainFlag", 0)

        # 7 天预报
        seven_day = []
        for item in grid_data.get("sevenDayWeatherForecast", [])[:7]:
            if isinstance(item, dict):
                max_t = item.get("maxTemperature")
                min_t = item.get("minTemperature")
                # 过滤占位记录
                is_placeholder = (
                    (max_t is None or max_t == 0)
                    and (min_t is None or min_t == 0)
                    and item.get("weather", "") == "晴"
                    and item.get("windDirection", "") == "西北风"
                )
                if is_placeholder:
                    if item.get("dateOrRimeTagStr", "") not in ("昨天", "今天"):
                        continue
                seven_day.append(
                    {
                        "date": item.get("dateOrRimeStr", ""),
                        "label": item.get("dateOrRimeTagStr", ""),
                        "weather_day": item.get("weather", ""),
                        "weather_night": item.get("eveningWeather", ""),
                        "temp_max": max_t,
                        "temp_min": min_t,
                        "wind_dir": item.get("windDirection", ""),
                        "wind_power": item.get("windPowerLevel", ""),
                    }
                )
        station_realtime["seven_day_forecast"] = seven_day

        # 逐时预报（取未来 12 小时）
        hourly = []
        for item in grid_data.get("oneDay24WeatherForecast", [])[:24]:
            if isinstance(item, dict):
                temp_val = item.get("temperature")
                is_placeholder = (
                    (temp_val is None or temp_val == 0)
                    and item.get("weather", "") == "晴"
                    and item.get("windDirection", "") == "西北风"
                )
                if is_placeholder:
                    continue
                hourly.append(
                    {
                        "time": item.get("dateOrRimeStr", ""),
                        "weather": item.get("weather", ""),
                        "temp": temp_val,
                        "wind_dir": item.get("windDirection", ""),
                        "wind_power": item.get("windPowerLevel", ""),
                    }
                )
        station_realtime["hourly_forecast"] = hourly[:12]

    return station_realtime


# ═══════════════════════════════════════════════════════════════════════════════
# 报告组装
# ═══════════════════════════════════════════════════════════════════════════════


def build_report(
    row: dict[str, Any],
    bounds: Bounds,
    frames: list[Frame],
    debug_image: str,
    grid_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """组装完整的预报报告。

    这是 nowcast 模块的核心协调函数，按以下步骤执行：
        1. 提取气象站数据
        2. 生成球场掩膜并估计运动
        3. 帧质量评估和趋势分析
        4. 上游回波检测
        5. 当前和外推时段的回波统计
        6. 降雨概率、置信度、冲突检测
        7. 四层风险引擎计算
        8. 可视化输出（调试图 + 雷达帧 + 拼图）
        9. 可打性评分
        10. 拼装最终 JSON 报告

    Args:
        row: first_row() 返回的 API 数据记录。
        bounds: parse_bounds() 解析的地理边界。
        frames: load_frames() 加载的帧列表。
        debug_image: 调试图输出路径（空字符串则跳过）。
        grid_data: 格点 API 数据（可选）。

    Returns:
        包含所有预报信息的完整报告字典。
    """
    latest = frames[-1]
    height, width = latest.dbz.shape
    grid_realtime = extract_station_and_forecast_data(row, grid_data)
    mask = court_mask(bounds, width, height)
    dx, dy, motion_consistency = estimate_motion(frames)

    # ---- 帧质量评估 ----
    quality_scores = []
    for i, f in enumerate(frames):
        prev = frames[i - 1].dbz if i > 0 else None
        quality_scores.append(frame_quality(f.dbz, mask, prev))

    # ---- 双窗口趋势分析 ----
    frames_dbz = [f.dbz for f in frames]
    trends = compute_trends(frames_dbz, mask, quality_scores)

    # ---- 上游回波检测 ----
    upstream = detect_upstream_echo(latest.dbz, mask, dx, dy, steps=5)

    # ---- 当前回波统计 ----
    current_stats = summarize_area(latest.dbz, mask)
    rain_probability: dict[str, float] = {}
    confidence: dict[str, str] = {}
    max_dbz_nearby: dict[str, int] = {}
    coverage_ratio: dict[str, float] = {}
    conflicts = set()

    # ---- 各时段外推统计 ----
    for label, steps in HORIZONS.items():
        future = translate_dbz(latest.dbz, dx, dy, steps)
        stats = summarize_area(future, mask)
        qpf_rain = check_qpf_rain(row, steps)
        prob = probability_from_stats(stats, steps, motion_consistency, qpf_rain)
        rain_probability[label] = prob
        confidence[label] = confidence_label(steps, motion_consistency, len(frames))
        max_dbz_nearby[label] = int(round(stats["max_dbz"]))
        coverage_ratio[label] = round(stats["playable_coverage"], 4)
        # 冲突检测：雷达和 QPF 不一致
        if prob > 0.3 and not qpf_rain:
            conflicts.add("radar_qpf_disagreement")
        elif prob < 0.3 and qpf_rain:
            conflicts.add("qpf_rain_without_radar")

    radar_has_echo = current_stats["max_dbz"] >= RADAR_ECHO_THRESHOLD_DBZ
    radar_has_playable = current_stats["max_dbz"] >= PLAYABLE_RAIN_THRESHOLD_DBZ
    qpf_has_rain_any = check_qpf_rain(row, 20)

    if motion_consistency < 0.5:
        conflicts.add("low_motion_confidence")

    # ---- 诊断提示 ----
    debug_hints = []
    if "radar_qpf_disagreement" in conflicts:
        debug_hints.append("CAPPI detects echo but QPF reports no rain.")
    if "qpf_rain_without_radar" in conflicts:
        debug_hints.append("QPF reports rain but CAPPI is clear.")
    if radar_has_echo and not radar_has_playable:
        debug_hints.append("Weak echo detected (>=15, <25 dBZ).")

    diagnostics = {
        "signals": {
            "radar_has_echo": bool(radar_has_echo),
            "radar_has_playable_rain_echo": bool(radar_has_playable),
            "qpf_has_rain": bool(qpf_has_rain_any),
            "motion_consistency": round(motion_consistency, 3),
        },
        "conflicts": list(conflicts),
        "debug_hints": debug_hints,
    }

    # ---- 四层风险引擎 ----
    qpf6min_all_zero = not qpf_has_rain_any
    rain_flag = grid_realtime.get("rain_2h_flag", 0) or 0
    hourly = grid_realtime.get("hourly_forecast", [])

    risk_scores = compute_risk_scores(
        current_stats=current_stats,
        rain_probability=rain_probability,
        trends=trends,
        upstream=upstream,
        grid_realtime=grid_realtime,
        qpf6min_all_zero=qpf6min_all_zero,
        rain_flag=rain_flag,
        motion_consistency=motion_consistency,
        hourly_forecast=hourly,
    )

    # ---- 可视化输出 ----
    if debug_image:
        create_debug_image(latest, bounds, mask, Path(debug_image))

    frames_dir = (
        Path(debug_image).parent / "radar_frames"
        if debug_image
        else Path("output/radar_frames")
    )
    radar_frame_entries = save_radar_frames(frames, bounds, mask, frames_dir)
    contact_sheet = create_radar_contact_sheet(
        radar_frame_entries, frames_dir.parent / "radar_contact_sheet.jpg"
    )

    # ---- 组装报告 ----
    court_x, court_y = lon_lat_to_pixel(
        COURT["lon"], COURT["lat"], bounds, width, height
    )
    report = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_frame_time": latest.timestamp.isoformat(),
        "court": {**COURT, "radius_km": RADIUS_KM},
        "radar_frames": radar_frame_entries,
        "cappi": {
            "bounds": [
                [bounds.min_lat, bounds.min_lon],
                [bounds.max_lat, bounds.max_lon],
            ],
            "image_size": {"width": width, "height": height},
            "frame_count": len(frames),
            "frame_times": [f.timestamp.isoformat() for f in frames],
            "latest_url": latest.url,
        },
        "mapping_debug": {
            "court_pixel": {"x": round(court_x, 2), "y": round(court_y, 2)},
            "sample_pixels_in_5km_radius": int(mask.sum()),
            "debug_image": debug_image,
            "radar_contact_sheet": contact_sheet,
        },
        "motion": {
            "dx_pixels_per_6min": round(dx, 3),
            "dy_pixels_per_6min": round(dy, 3),
            "consistency": round(motion_consistency, 3),
        },
        "current": {
            "max_dbz_nearby": int(round(current_stats["max_dbz"])),
            "echo_coverage": round(current_stats["echo_coverage"], 4),
            "playable_coverage": round(current_stats["playable_coverage"], 4),
            "mean_rain_rate": round(current_stats["mean_rain_rate"], 2),
        },
        "trends": trends,
        "upstream_echo": upstream,
        "risk_scores": risk_scores,
        "rain_probability": rain_probability,
        "confidence": confidence,
        "max_dbz_nearby": max_dbz_nearby,
        "playable_coverage_ratio": coverage_ratio,
        "diagnostics": diagnostics,
        "frame_quality": [round(q, 2) for q in quality_scores],
        "station_realtime": grid_realtime,
        "playability": compute_playability(
            rain_probability=rain_probability,
            risk_scores=risk_scores,
            grid_realtime=grid_realtime,
            qpf_has_rain=qpf_has_rain_any,
            rain_flag=rain_flag,
            current_stats=current_stats,
        ),
        **official_summary(row),
    }
    return report
