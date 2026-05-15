#!/usr/bin/env python3
"""CAPPI radar nowcast MVP for 科技四路文体公园.

Reads a captured weather response, downloads the CAPPI PNG frames it references,
maps radar pixels to approximate dBZ, extrapolates motion with OpenCV optical
flow, and emits a JSON rain-probability report for one fixed tennis court.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
import urllib.request
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

from risk_engine import (
    frame_quality,
    compute_trends,
    detect_upstream_echo,
    compute_risk_scores,
    save_calibration_log,
)


# COURT = {
#     "id": "qiaoguang_commercial_centre",
#     "name": "侨光商业中心",
#     "lon": 113.54,
#     "lat": 22.20,
# }
COURT = {
    "id": "Keji 4th Road Tennis Court",
    "name": "科技四路网球场",
    "lon": 113.55,
    "lat": 22.39,
}

RADIUS_KM = 7.0
RADAR_ECHO_THRESHOLD_DBZ = 15
PLAYABLE_RAIN_THRESHOLD_DBZ = 25
ALPHA_THRESHOLD = 8
HORIZONS = {"30min": 5, "60min": 10, "120min": 20}
EARTH_RADIUS_KM = 6371.0088
SOURCE_TZ = timezone(timedelta(hours=8))

# GD121 API Constants (CAPPI radar + QPF)
API_URL_TEMPLATE = "https://wxc.gd121.cn/gdecloud/servlet/servletcityweatherall4?DISTRICTCODE=440402&LNG={lon}&LAT={lat}&FROM=binfen"
API_HEADERS = {
    "Host": "wxc.gd121.cn",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://mp.gd121.cn",
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 26_4_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.73(0x18004923) NetType/4G Language/zh_CN miniProgram/wx4e37a66956191c3a",
    "Referer": "https://mp.gd121.cn/",
    "Accept-Language": "en-US,en;q=0.9",
}

# Grid weather API (ra.gd121.cn) - precise grid-interpolated real-time weather
RA_API_URL_TEMPLATE = "https://ra.gd121.cn/grid/api/index/weatherInfo?longitude={lon}&latitude={lat}&FROM=binfen"
RA_API_HEADERS = {
    "Host": "ra.gd121.cn",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://mp.gd121.cn",
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 26_4_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.73(0x18004923) NetType/4G Language/zh_CN miniProgram/wx4e37a66956191c3a",
    "Referer": "https://mp.gd121.cn/",
    "Accept-Language": "en-US,en;q=0.9",
}

# Approximate CAPPI legend colors sampled from the displayed legend.
DBZ_PALETTE = [
    (5, (0, 221, 208)),
    (10, (0, 169, 214)),
    (15, (5, 51, 245)),
    (20, (0, 238, 0)),
    (25, (0, 214, 50)),
    (30, (0, 141, 31)),
    (35, (255, 242, 0)),
    (40, (229, 201, 0)),
    (45, (255, 140, 20)),
    (50, (255, 41, 41)),
    (55, (201, 20, 20)),
    (60, (123, 0, 0)),
    (65, (255, 77, 255)),
    (70, (153, 73, 191)),
]


@dataclass(frozen=True)
class Bounds:
    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float


@dataclass(frozen=True)
class Frame:
    timestamp: datetime
    url: str
    path: Path
    dbz: np.ndarray
    rgba: Image.Image


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CAPPI 5km rain nowcast MVP")
    parser.add_argument(
        "--response",
        default="response.txt",
        help="Captured JSON response containing cappi/cappi_bounds fields.",
    )
    parser.add_argument(
        "--cache-dir",
        default="data/cappi",
        help="Directory for downloaded CAPPI PNG files.",
    )
    parser.add_argument(
        "--output",
        default="output/forecast.json",
        help="Output JSON report path. Use '-' for stdout only.",
    )
    parser.add_argument(
        "--debug-image",
        default="output/debug_court_radius.png",
        help="Optional debug image path showing court and 5km radius. Use '' to skip.",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=12,
        help="Maximum newest CAPPI frames to use.",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run continuously, fetching data from the API.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=360,
        help="Interval in seconds between API fetches in daemon mode (default: 360).",
    )
    return parser.parse_args()


def load_response(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_weather_data(lon: float, lat: float) -> dict[str, Any]:
    url = API_URL_TEMPLATE.format(lon=lon, lat=lat)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(url, headers=API_HEADERS)
    with urllib.request.urlopen(req, context=ctx, timeout=30) as response:
        data = response.read().decode("utf-8")
        return json.loads(data)


def fetch_grid_weather(lon: float, lat: float) -> dict[str, Any] | None:
    """Fetch grid-interpolated real-time weather from ra.gd121.cn.

    Returns precise weather data at the exact coordinates (not from a distant station).
    Returns None on failure so the system can fall back gracefully.
    """
    url = RA_API_URL_TEMPLATE.format(lon=lon, lat=lat)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        req = urllib.request.Request(url, headers=RA_API_HEADERS)
        with urllib.request.urlopen(req, context=ctx, timeout=15) as response:
            raw = json.loads(response.read().decode("utf-8"))
            if raw.get("status") == 200 and "data" in raw:
                return raw["data"]
    except Exception as e:
        print(f"Warning: grid weather API failed: {e}", file=sys.stderr)
    return None


def first_row(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Response does not contain rows[0]")
    if not isinstance(rows[0], dict):
        raise ValueError("rows[0] is not an object")
    return rows[0]


def parse_bounds(row: dict[str, Any]) -> Bounds:
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
    match = re.search(r"CAPPI_\d+_(\d{14})\.png", url)
    if not match:
        raise ValueError(f"Cannot parse CAPPI timestamp from URL: {url}")
    return datetime.strptime(match.group(1), "%Y%m%d%H%M%S").replace(tzinfo=SOURCE_TZ)


def collect_cappi(row: dict[str, Any], max_frames: int) -> list[tuple[datetime, str]]:
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


def download_frame(url: str, cache_dir: Path) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    filename = url.rsplit("/", 1)[-1].replace("!wbdstyle", "")
    path = cache_dir / filename
    if path.exists() and path.stat().st_size > 0:
        return path
    request = urllib.request.Request(url, headers={"User-Agent": "nowcast-mvp/0.1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        path.write_bytes(response.read())
    return path


def cleanup_cache(cache_dir: Path, max_age_hours: float = 2.0) -> None:
    """Remove files in the cache directory older than max_age_hours."""
    if not cache_dir.exists():
        return
    now = time.time()
    for file_path in cache_dir.glob("*.png"):
        try:
            if file_path.is_file():
                mtime = file_path.stat().st_mtime
                if (now - mtime) > (max_age_hours * 3600):
                    file_path.unlink()
        except Exception as e:
            print(
                f"Warning: failed to delete old cache file {file_path}: {e}",
                file=sys.stderr,
            )


def image_to_dbz(image: Image.Image) -> np.ndarray:
    rgba = np.array(image.convert("RGBA"), dtype=np.uint8)
    rgb = rgba[:, :, :3].astype(np.int32)
    alpha = rgba[:, :, 3]

    palette_dbz = np.array([dbz for dbz, _ in DBZ_PALETTE], dtype=np.float32)
    palette_rgb = np.array([rgb for _, rgb in DBZ_PALETTE], dtype=np.int32)

    diff = rgb[:, :, None, :] - palette_rgb[None, None, :, :]
    dist2 = np.sum(diff * diff, axis=3)
    nearest = np.argmin(dist2, axis=2)
    min_dist = np.sqrt(np.min(dist2, axis=2).astype(np.float32))
    chroma = np.max(rgb, axis=2) - np.min(rgb, axis=2)

    dbz = np.zeros(alpha.shape, dtype=np.float32)
    valid = (alpha > ALPHA_THRESHOLD) & (chroma > 35) & (min_dist < 120.0)
    dbz[valid] = palette_dbz[nearest[valid]]
    return dbz


def load_frames(entries: list[tuple[datetime, str]], cache_dir: Path) -> list[Frame]:
    frames: list[Frame] = []
    for timestamp, url in entries:
        path = download_frame(url, cache_dir)
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


def lon_lat_to_pixel(
    lon: float, lat: float, bounds: Bounds, width: int, height: int
) -> tuple[float, float]:
    x = (lon - bounds.min_lon) / (bounds.max_lon - bounds.min_lon) * width
    y = (bounds.max_lat - lat) / (bounds.max_lat - bounds.min_lat) * height
    return x, y


def pixel_grids(
    bounds: Bounds, width: int, height: int
) -> tuple[np.ndarray, np.ndarray]:
    xs = np.arange(width, dtype=np.float64) + 0.5
    ys = np.arange(height, dtype=np.float64) + 0.5
    lon = bounds.min_lon + xs / width * (bounds.max_lon - bounds.min_lon)
    lat = bounds.max_lat - ys / height * (bounds.max_lat - bounds.min_lat)
    return np.meshgrid(lon, lat)


def haversine_km(
    lon1: np.ndarray, lat1: np.ndarray, lon2: float, lat2: float
) -> np.ndarray:
    lon1_rad = np.radians(lon1)
    lat1_rad = np.radians(lat1)
    lon2_rad = math.radians(lon2)
    lat2_rad = math.radians(lat2)
    dlon = lon1_rad - lon2_rad
    dlat = lat1_rad - lat2_rad
    a = (
        np.sin(dlat / 2) ** 2
        + np.cos(lat1_rad) * math.cos(lat2_rad) * np.sin(dlon / 2) ** 2
    )
    return EARTH_RADIUS_KM * 2 * np.arcsin(np.sqrt(a))


def court_mask(bounds: Bounds, width: int, height: int) -> np.ndarray:
    lon_grid, lat_grid = pixel_grids(bounds, width, height)
    return haversine_km(lon_grid, lat_grid, COURT["lon"], COURT["lat"]) <= RADIUS_KM


def estimate_motion(frames: list[Frame]) -> tuple[float, float, float]:
    motions: list[tuple[float, float]] = []
    for prev, curr in zip(frames[:-1], frames[1:]):
        prev_gray = np.clip(prev.dbz / 70.0 * 255.0, 0, 255).astype(np.uint8)
        curr_gray = np.clip(curr.dbz / 70.0 * 255.0, 0, 255).astype(np.uint8)
        flow = cv2.calcOpticalFlowFarneback(
            prev_gray,
            curr_gray,
            None,
            pyr_scale=0.5,
            levels=3,
            winsize=31,
            iterations=3,
            poly_n=5,
            poly_sigma=1.2,
            flags=0,
        )
        signal = (prev.dbz >= 5) | (curr.dbz >= 5)
        if int(signal.sum()) < 20:
            continue
        dx = float(np.median(flow[:, :, 0][signal]))
        dy = float(np.median(flow[:, :, 1][signal]))
        if math.isfinite(dx) and math.isfinite(dy):
            motions.append((dx, dy))
    if not motions:
        return 0.0, 0.0, 0.0
    arr = np.array(motions, dtype=np.float32)
    dx = float(np.median(arr[:, 0]))
    dy = float(np.median(arr[:, 1]))
    consistency = float(max(0.0, 1.0 - np.mean(np.std(arr, axis=0)) / 8.0))
    return dx, dy, consistency


def translate_dbz(dbz: np.ndarray, dx: float, dy: float, steps: int) -> np.ndarray:
    matrix = np.array(
        [[1.0, 0.0, dx * steps], [0.0, 1.0, dy * steps]], dtype=np.float32
    )
    return cv2.warpAffine(
        dbz,
        matrix,
        (dbz.shape[1], dbz.shape[0]),
        flags=cv2.INTER_NEAREST,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=0,
    )


def dbz_to_rain_rate(dbz: float) -> float:
    """Marshall-Palmer Z-R relationship (Z = 200 * R^1.6)"""
    if dbz <= 0:
        return 0.0
    z = 10 ** (dbz / 10.0)
    return (z / 200.0) ** (1 / 1.6)


def summarize_area(dbz: np.ndarray, mask: np.ndarray) -> dict[str, float]:
    values = dbz[mask]
    has_echo = values >= RADAR_ECHO_THRESHOLD_DBZ
    has_playable = values >= PLAYABLE_RAIN_THRESHOLD_DBZ

    echo_coverage = float(has_echo.mean()) if values.size else 0.0
    playable_coverage = float(has_playable.mean()) if values.size else 0.0
    max_dbz = float(values.max()) if values.size else 0.0

    mean_rain_rate = 0.0
    if has_playable.any():
        rain_rates = [dbz_to_rain_rate(float(v)) for v in values[has_playable]]
        mean_rain_rate = float(np.mean(rain_rates))

    return {
        "echo_coverage": echo_coverage,
        "playable_coverage": playable_coverage,
        "max_dbz": max_dbz,
        "mean_rain_rate": mean_rain_rate,
    }


def probability_from_stats(
    stats: dict[str, float],
    horizon_steps: int,
    motion_consistency: float,
    qpf_has_rain: bool,
) -> float:
    coverage = stats["playable_coverage"]
    max_dbz = stats["max_dbz"]
    rain_rate = stats["mean_rain_rate"]

    if max_dbz < PLAYABLE_RAIN_THRESHOLD_DBZ:
        base_prob = min(0.1, stats["echo_coverage"] * 0.5)
    else:
        coverage_score = min(1.0, coverage / 0.15)
        intensity_score = min(1.0, rain_rate / 5.0)
        raw = 0.2 + 0.5 * coverage_score + 0.3 * intensity_score
        horizon_discount = {5: 1.0, 10: 0.85, 20: 0.62}.get(horizon_steps, 0.7)
        base_prob = raw * horizon_discount * (0.8 + 0.2 * motion_consistency)

    if base_prob > 0.2 and not qpf_has_rain:
        base_prob *= 0.5
    elif base_prob < 0.2 and qpf_has_rain:
        base_prob = max(base_prob, 0.3)

    return round(float(max(0.0, min(0.99, base_prob))), 2)


def confidence_label(
    horizon_steps: int, motion_consistency: float, frame_count: int
) -> str:
    if horizon_steps >= 20:
        return "low"
    if frame_count >= 4 and motion_consistency >= 0.55:
        return "high" if horizon_steps == 5 else "medium"
    return "medium" if horizon_steps == 5 else "low"


def create_debug_image(
    latest: Frame, bounds: Bounds, mask: np.ndarray, path: Path
) -> None:
    base = latest.rgba.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    mask_img = Image.fromarray(np.where(mask, 80, 0).astype(np.uint8), mode="L")
    radius_layer = Image.new("RGBA", base.size, (255, 255, 255, 0))
    radius_layer.putalpha(mask_img)
    overlay = Image.alpha_composite(overlay, radius_layer)

    x, y = lon_lat_to_pixel(COURT["lon"], COURT["lat"], bounds, base.width, base.height)
    draw = ImageDraw.Draw(overlay)
    draw.ellipse(
        (x - 6, y - 6, x + 6, y + 6),
        fill=(255, 0, 0, 255),
        outline=(255, 255, 255, 255),
        width=2,
    )
    draw.text((x + 10, y - 10), COURT["name"], fill=(255, 0, 0, 255))

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.alpha_composite(base, overlay).save(path)


def save_radar_frames(
    frames: list[Frame], bounds: Bounds, mask: np.ndarray, output_dir: Path
) -> list[dict[str, str]]:
    """Save each CAPPI frame as individual PNG with court marker overlay.

    Returns list of {time, path, timestamp} for the frontend timeline player.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    # Clean old frames
    for old in output_dir.glob("frame_*.png"):
        try:
            old.unlink()
        except OSError:
            pass

    result = []
    for i, f in enumerate(frames):
        base = f.rgba.convert("RGBA")
        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))

        # Draw 5km radius
        mask_img = Image.fromarray(np.where(mask, 60, 0).astype(np.uint8), mode="L")
        radius_layer = Image.new("RGBA", base.size, (255, 255, 255, 0))
        radius_layer.putalpha(mask_img)
        overlay = Image.alpha_composite(overlay, radius_layer)

        draw = ImageDraw.Draw(overlay)
        # Court marker
        x, y = lon_lat_to_pixel(
            COURT["lon"], COURT["lat"], bounds, base.width, base.height
        )
        draw.ellipse(
            (x - 5, y - 5, x + 5, y + 5),
            fill=(255, 0, 0, 255),
            outline=(255, 255, 255, 200),
            width=2,
        )

        # Timestamp label
        time_str = f.timestamp.strftime("%H:%M")
        draw.text((8, 8), time_str, fill=(255, 255, 255, 220))

        composed = Image.alpha_composite(base, overlay)
        fname = f"frame_{i:02d}_{f.timestamp.strftime('%H%M')}.png"
        out_path = output_dir / fname
        composed.save(out_path)

        result.append(
            {
                "time": time_str,
                "path": str(out_path),
                "timestamp": f.timestamp.isoformat(),
            }
        )

    return result


def check_qpf_rain(row: dict[str, Any], steps: int) -> bool:
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


def extract_grid_realtime(grid_data: dict[str, Any] | None) -> dict[str, Any]:
    """Extract real-time weather from ra.gd121.cn grid API response.

    This data is grid-interpolated at the exact court coordinates,
    unlike the old sk_ data which came from a station 12km away.
    """
    if not grid_data:
        return {"source": "unavailable"}

    nw = grid_data.get("nowWeather", {})
    rain_dto = grid_data.get("nowTwoHourResDto", {})

    # Parse humidity string like "76%" -> 76.0
    humidity_str = nw.get("humidity", "0")
    try:
        humidity = float(str(humidity_str).replace("%", ""))
    except (ValueError, TypeError):
        humidity = 0.0

    # Extract 7-day forecast for "天气底色" judgment
    # Filter out API placeholder entries: the ra.gd121.cn API returns unfilled
    # defaults (temp=0, weather=晴, wind=西北风) for days it hasn't computed.
    seven_day = []
    for item in grid_data.get("sevenDayWeatherForecast", [])[:7]:
        if isinstance(item, dict):
            max_t = item.get("maxTemperature")
            min_t = item.get("minTemperature")
            # Placeholder check: both temps are 0 (or None) AND weather is the
            # generic default "晴" — real clear-sky forecasts still have temps.
            is_placeholder = (
                (max_t is None or max_t == 0)
                and (min_t is None or min_t == 0)
                and item.get("weather", "") == "晴"
                and item.get("windDirection", "") == "西北风"
            )
            if is_placeholder:
                # Keep "today" even if partially placeholder (it often has
                # valid daytime data), skip future placeholder days entirely.
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

    # Extract upcoming hourly forecast (next 12 hours)
    # Same placeholder filtering as 7-day: skip entries where temp=0 +
    # weather=晴 + wind=西北风 which are unfilled API defaults.
    hourly = []
    for item in grid_data.get("oneDay24WeatherForecast", [])[:24]:
        if isinstance(item, dict):
            temp = item.get("temperature")
            is_placeholder = (
                (temp is None or temp == 0)
                and item.get("weather", "") == "晴"
                and item.get("windDirection", "") == "西北风"
            )
            if is_placeholder:
                continue
            hourly.append(
                {
                    "time": item.get("dateOrRimeStr", ""),
                    "weather": item.get("weather", ""),
                    "temp": temp,
                    "wind_dir": item.get("windDirection", ""),
                    "wind_power": item.get("windPowerLevel", ""),
                }
            )
    # Cap at 12 valid entries
    hourly = hourly[:12]

    return {
        "source": "grid_interpolated",
        "observation_time": nw.get("updateTime", ""),
        "temperature": nw.get("temperature"),
        "humidity_pct": humidity,
        "wind_direction": nw.get("windDirection", ""),
        "wind_power_level": nw.get("windPowerLevel", ""),
        "wind_speed_mps": nw.get("windSpeed"),
        "weather_state": nw.get("weather", ""),
        "aqi": nw.get("airQualityAqi"),
        "aqi_level": nw.get("airQuality", ""),
        "rain_2h_message": rain_dto.get("message", ""),
        "rain_2h_flag": rain_dto.get("rainFlag", 0),
        "hourly_forecast": hourly,
        "seven_day_forecast": seven_day,
    }


def build_report(
    row: dict[str, Any],
    bounds: Bounds,
    frames: list[Frame],
    debug_image: str,
    grid_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    latest = frames[-1]
    height, width = latest.dbz.shape
    grid_realtime = extract_grid_realtime(grid_data)
    mask = court_mask(bounds, width, height)
    dx, dy, motion_consistency = estimate_motion(frames)

    # ---- Frame quality control ----
    quality_scores = []
    for i, f in enumerate(frames):
        prev = frames[i - 1].dbz if i > 0 else None
        quality_scores.append(frame_quality(f.dbz, mask, prev))

    # ---- Dual-window trend analysis ----
    frames_dbz = [f.dbz for f in frames]
    trends = compute_trends(frames_dbz, mask, quality_scores)

    # ---- Upstream echo detection ----
    upstream = detect_upstream_echo(latest.dbz, mask, dx, dy, steps=5)

    # ---- Original radar analysis ----
    current_stats = summarize_area(latest.dbz, mask)
    rain_probability: dict[str, float] = {}
    confidence: dict[str, str] = {}
    max_dbz_nearby: dict[str, int] = {}
    coverage_ratio: dict[str, float] = {}
    conflicts = set()

    for label, steps in HORIZONS.items():
        future = translate_dbz(latest.dbz, dx, dy, steps)
        stats = summarize_area(future, mask)
        qpf_rain = check_qpf_rain(row, steps)
        prob = probability_from_stats(stats, steps, motion_consistency, qpf_rain)
        rain_probability[label] = prob
        confidence[label] = confidence_label(steps, motion_consistency, len(frames))
        max_dbz_nearby[label] = int(round(stats["max_dbz"]))
        coverage_ratio[label] = round(stats["playable_coverage"], 4)
        if prob > 0.3 and not qpf_rain:
            conflicts.add("radar_qpf_disagreement")
        elif prob < 0.3 and qpf_rain:
            conflicts.add("qpf_rain_without_radar")

    radar_has_echo = current_stats["max_dbz"] >= RADAR_ECHO_THRESHOLD_DBZ
    radar_has_playable = current_stats["max_dbz"] >= PLAYABLE_RAIN_THRESHOLD_DBZ
    qpf_has_rain_any = check_qpf_rain(row, 20)

    if motion_consistency < 0.5:
        conflicts.add("low_motion_confidence")

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

    # ---- Four-layer risk engine ----
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

    if debug_image:
        create_debug_image(latest, bounds, mask, Path(debug_image))

    # Save individual frames for timeline player
    frames_dir = (
        Path(debug_image).parent / "radar_frames"
        if debug_image
        else Path("output/radar_frames")
    )
    radar_frame_entries = save_radar_frames(frames, bounds, mask, frames_dir)

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
        **official_summary(row),
    }
    return report


def run_once(args: argparse.Namespace) -> None:
    grid_data = None
    if args.daemon:
        print(f"Fetching live data for lon={COURT['lon']}, lat={COURT['lat']}...")
        payload = fetch_weather_data(COURT["lon"], COURT["lat"])
        grid_data = fetch_grid_weather(COURT["lon"], COURT["lat"])
    else:
        print(f"Loading local response from {args.response}...")
        payload = load_response(Path(args.response))

    row = first_row(payload)
    bounds = parse_bounds(row)
    entries = collect_cappi(row, args.max_frames)
    frames = load_frames(entries, Path(args.cache_dir))
    report = build_report(row, bounds, frames, args.debug_image, grid_data)

    # Save calibration log for future backtesting
    risk_scores = report.get("risk_scores", {})
    save_calibration_log(report, risk_scores)

    text = json.dumps(report, ensure_ascii=False, indent=2)

    if args.output == "-":
        print(text)
    else:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Wrote {output}")

    # Auto-cleanup old frames to prevent unlimited disk usage
    cleanup_cache(Path(args.cache_dir), max_age_hours=2.0)


def main() -> int:
    args = parse_args()
    if args.daemon:
        print(f"Starting in daemon mode. Interval: {args.interval}s")
        while True:
            try:
                run_once(args)
            except Exception as e:
                print(
                    f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Error in daemon iteration: {e}",
                    file=sys.stderr,
                )
            print(f"Waiting {args.interval} seconds...")
            time.sleep(args.interval)
    else:
        run_once(args)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
