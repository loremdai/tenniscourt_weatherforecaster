"""雷达图像处理层。

负责 CAPPI 雷达图像的全部计算密集型处理：
    - 像素颜色 → dBZ 反射率反算（最近邻色卡匹配）
    - 经纬度 ↔ 像素坐标互转
    - 球场分析半径掩膜（Haversine 球面距离）
    - 光流法运动估计（OpenCV Farneback）
    - dBZ 帧外推（仿射平移）
    - 区域回波统计（覆盖率、最大 dBZ、降雨率）
    - 降雨概率计算与置信度评级
    - 调试图与雷达帧可视化输出
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw

from config import (
    COURT,
    RADIUS_KM,
    RADAR_ECHO_THRESHOLD_DBZ,
    PLAYABLE_RAIN_THRESHOLD_DBZ,
    ALPHA_THRESHOLD,
    EARTH_RADIUS_KM,
    DBZ_PALETTE,
)


# ═══════════════════════════════════════════════════════════════════════════════
# 像素 ↔ dBZ 转换
# ═══════════════════════════════════════════════════════════════════════════════


def image_to_dbz(image: Image.Image) -> np.ndarray:
    """将 CAPPI 雷达 RGBA 图像转换为 dBZ（反射率因子）二维数组。

    转换原理：
        1. 将图像转为 RGBA 格式，提取 RGB 通道和透明度
        2. 用 config.DBZ_PALETTE（色卡查找表）对每个像素做最近邻颜色匹配
        3. 匹配成功的像素赋予对应的 dBZ 值，否则为 0

    过滤条件（用于排除底图背景和非气象像素）：
        - alpha > ALPHA_THRESHOLD (8)：排除透明底图
        - chroma > 35：排除灰色/黑色底图文字
        - min_dist < 120：排除与色卡偏差过大的噪点

    Args:
        image: PIL Image 对象（任意模式，内部会转为 RGBA）。

    Returns:
        float32 二维数组，形状 (height, width)，值为 dBZ (0~70)。
    """
    rgba = np.array(image.convert("RGBA"), dtype=np.uint8)
    rgb = rgba[:, :, :3].astype(np.int32)
    alpha = rgba[:, :, 3]

    # 构建色卡查找表
    palette_dbz = np.array([dbz for dbz, _ in DBZ_PALETTE], dtype=np.float32)
    palette_rgb = np.array([rgb_val for _, rgb_val in DBZ_PALETTE], dtype=np.int32)

    # 计算每个像素与色卡中所有颜色的欧氏距离
    diff = rgb[:, :, None, :] - palette_rgb[None, None, :, :]
    dist2 = np.sum(diff * diff, axis=3)
    nearest = np.argmin(dist2, axis=2)
    min_dist = np.sqrt(np.min(dist2, axis=2).astype(np.float32))

    # 饱和度过滤：max(R,G,B) - min(R,G,B) > 35
    chroma = np.max(rgb, axis=2) - np.min(rgb, axis=2)

    # 只有满足三个条件的像素才被视为有效气象回波
    dbz = np.zeros(alpha.shape, dtype=np.float32)
    valid = (alpha > ALPHA_THRESHOLD) & (chroma > 35) & (min_dist < 120.0)
    dbz[valid] = palette_dbz[nearest[valid]]
    return dbz


# ═══════════════════════════════════════════════════════════════════════════════
# 地理坐标 ↔ 像素坐标
# ═══════════════════════════════════════════════════════════════════════════════


def lon_lat_to_pixel(
    lon: float, lat: float, bounds: Any, width: int, height: int
) -> tuple[float, float]:
    """将经纬度坐标转换为图像像素坐标。

    使用线性插值将地理坐标映射到图像的像素空间。
    注意：纬度方向是反转的（图像 y=0 在上方，对应最大纬度）。

    Args:
        lon: 经度。
        lat: 纬度。
        bounds: Bounds 对象，包含 min_lat/max_lat/min_lon/max_lon。
        width: 图像宽度（像素）。
        height: 图像高度（像素）。

    Returns:
        (x, y) 像素坐标元组。
    """
    x = (lon - bounds.min_lon) / (bounds.max_lon - bounds.min_lon) * width
    y = (bounds.max_lat - lat) / (bounds.max_lat - bounds.min_lat) * height
    return x, y


def pixel_grids(bounds: Any, width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
    """生成图像每个像素对应的经纬度网格。

    返回两个二维数组，分别存储每个像素中心点的经度和纬度。
    用于 Haversine 距离计算等需要逐像素地理坐标的场景。

    Args:
        bounds: Bounds 对象。
        width: 图像宽度。
        height: 图像高度。

    Returns:
        (lon_grid, lat_grid) 网格数组元组，形状均为 (height, width)。
    """
    xs = np.arange(width, dtype=np.float64) + 0.5
    ys = np.arange(height, dtype=np.float64) + 0.5
    lon = bounds.min_lon + xs / width * (bounds.max_lon - bounds.min_lon)
    lat = bounds.max_lat - ys / height * (bounds.max_lat - bounds.min_lat)
    return np.meshgrid(lon, lat)


def haversine_km(
    lon1: np.ndarray, lat1: np.ndarray, lon2: float, lat2: float
) -> np.ndarray:
    """Haversine 公式计算球面距离（支持 numpy 批量运算）。

    计算一组经纬度点到单个参考点之间的大圆距离（公里）。
    使用 WGS-84 平均地球半径。

    Args:
        lon1: 经度数组。
        lat1: 纬度数组。
        lon2: 参考点经度。
        lat2: 参考点纬度。

    Returns:
        距离数组（公里），形状与 lon1/lat1 一致。
    """
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


def court_mask(bounds: Any, width: int, height: int) -> np.ndarray:
    """生成球场分析半径的布尔掩膜。

    以 COURT 坐标为圆心、RADIUS_KM 为半径，标记所有落在分析范围内的像素。
    后续所有"球场附近"的统计都基于此掩膜。

    Args:
        bounds: Bounds 对象。
        width: 图像宽度。
        height: 图像高度。

    Returns:
        布尔二维数组，True 表示该像素在球场分析半径内。
    """
    lon_grid, lat_grid = pixel_grids(bounds, width, height)
    return haversine_km(lon_grid, lat_grid, COURT["lon"], COURT["lat"]) <= RADIUS_KM


# ═══════════════════════════════════════════════════════════════════════════════
# 光流运动估计与帧外推
# ═══════════════════════════════════════════════════════════════════════════════


def estimate_motion(frames: list) -> tuple[float, float, float]:
    """使用 Farneback 光流法估计雷达回波的运动方向和速度。

    对相邻帧两两计算稠密光流，取所有有效回波像素的中位数作为运动向量，
    再对多帧结果取中位数作为最终估计。同时计算一致性分数，反映运动方向
    在时间序列上的稳定程度。

    一致性分数说明：
        - > 0.7：运动方向稳定，外推可信度较高
        - 0.4~0.7：运动方向有波动，外推仅供参考
        - < 0.4：运动不一致，可能有多团回波或光流噪声

    Args:
        frames: Frame 对象列表（至少 2 帧），每个含 .dbz 属性。

    Returns:
        (dx, dy, consistency) 元组：
        - dx: 水平运动量（像素/6分钟），正值向右
        - dy: 垂直运动量（像素/6分钟），正值向下
        - consistency: 一致性分数 (0.0~1.0)
    """
    motions: list[tuple[float, float]] = []
    for prev, curr in zip(frames[:-1], frames[1:]):
        # 将 dBZ 归一化到 0-255 灰度，作为光流的输入
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

        # 只在有足够回波信号的区域统计运动（避免背景噪声干扰）
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
    # 一致性：标准差越小越一致，除以 8.0 归一化
    consistency = float(max(0.0, 1.0 - np.mean(np.std(arr, axis=0)) / 8.0))
    return dx, dy, consistency


def translate_dbz(dbz: np.ndarray, dx: float, dy: float, steps: int) -> np.ndarray:
    """沿运动方向平移 dBZ 帧（线性外推预测）。

    使用 OpenCV 仿射变换将当前 dBZ 帧按运动向量平移指定步数，
    模拟未来某时刻的雷达回波分布。边界以 0 填充。

    Args:
        dbz: 当前 dBZ 二维数组。
        dx: 每步水平位移（像素）。
        dy: 每步垂直位移（像素）。
        steps: 外推步数（每步对应 6 分钟）。

    Returns:
        外推后的 dBZ 二维数组，形状不变。
    """
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


# ═══════════════════════════════════════════════════════════════════════════════
# 区域统计与概率计算
# ═══════════════════════════════════════════════════════════════════════════════


def dbz_to_rain_rate(dbz: float) -> float:
    """Marshall-Palmer Z-R 关系将 dBZ 转换为降雨率（mm/h）。

    经验公式：Z = 200 × R^1.6，其中 Z = 10^(dBZ/10)。
    这是气象学中最常用的雷达降雨率估算方法。

    Args:
        dbz: 反射率因子（dBZ）。

    Returns:
        估算降雨率（mm/h），dBZ ≤ 0 时返回 0。
    """
    if dbz <= 0:
        return 0.0
    z = 10 ** (dbz / 10.0)
    return (z / 200.0) ** (1 / 1.6)


def summarize_area(dbz: np.ndarray, mask: np.ndarray) -> dict[str, float]:
    """统计球场分析半径内的雷达回波特征。

    在掩膜区域内计算四个核心指标：
        - echo_coverage: 有回波（≥15 dBZ）的像素占比
        - playable_coverage: 影响打球（≥25 dBZ）的像素占比
        - max_dbz: 区域内最大 dBZ 值
        - mean_rain_rate: 有效回波像素的平均降雨率

    Args:
        dbz: dBZ 二维数组。
        mask: 布尔掩膜（True = 在分析范围内）。

    Returns:
        包含上述四个指标的字典。
    """
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
    """根据区域统计指标计算降雨概率。

    综合考虑覆盖率、降雨率、时间衰减、运动一致性和官方 QPF 交叉校验：
        - 弱回波（< 25 dBZ）：概率上限 0.1
        - 强回波：基于覆盖率(50%) + 降雨率(30%) + 基线(20%) 计算
        - 远期预报有时间折扣（30min=1.0, 60min=0.85, 120min=0.62）
        - QPF 无雨时概率减半，QPF 有雨时概率至少 0.3

    Args:
        stats: ``summarize_area`` 返回的统计字典。
        horizon_steps: 预报时段步数（5/10/20，对应 30/60/120 分钟）。
        motion_consistency: 运动一致性分数。
        qpf_has_rain: 官方 QPF 是否预报有雨。

    Returns:
        降雨概率 (0.0~0.99)，保留两位小数。
    """
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

    # QPF 交叉校验：雷达和官方预报不一致时进行概率修正
    if base_prob > 0.2 and not qpf_has_rain:
        base_prob *= 0.5  # 雷达有回波但 QPF 无雨，降低概率
    elif base_prob < 0.2 and qpf_has_rain:
        base_prob = max(base_prob, 0.3)  # QPF 有雨但雷达弱，抬升概率

    return round(float(max(0.0, min(0.99, base_prob))), 2)


def confidence_label(
    horizon_steps: int, motion_consistency: float, frame_count: int
) -> str:
    """根据预报时段和数据质量评定置信度等级。

    评定规则：
        - 120 分钟以上：一律 low（光流外推超过 1 小时可信度很低）
        - 帧数 ≥ 4 且一致性 ≥ 0.55 时：30min=high, 60min=medium
        - 其余情况：30min=medium, 60min=low

    Args:
        horizon_steps: 预报步数。
        motion_consistency: 运动一致性分数。
        frame_count: 可用帧数。

    Returns:
        "high"、"medium" 或 "low"。
    """
    if horizon_steps >= 20:
        return "low"
    if frame_count >= 4 and motion_consistency >= 0.55:
        return "high" if horizon_steps == 5 else "medium"
    return "medium" if horizon_steps == 5 else "low"


# ═══════════════════════════════════════════════════════════════════════════════
# 可视化输出
# ═══════════════════════════════════════════════════════════════════════════════


def create_debug_image(latest: Any, bounds: Any, mask: np.ndarray, path: Path) -> None:
    """生成调试图：在最新雷达帧上叠加球场标记和分析半径。

    输出一张 RGBA PNG 图片，其中：
        - 底图是最新一帧 CAPPI 雷达图
        - 半透明白色区域标示分析半径
        - 红色圆点标示球场位置
        - 红色文字标示球场名称

    Args:
        latest: 最新的 Frame 对象（含 .rgba 属性）。
        bounds: Bounds 对象。
        mask: 分析半径布尔掩膜。
        path: 输出图片路径。
    """
    base = latest.rgba.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # 绘制分析半径（半透明白色覆盖层）
    mask_img = Image.fromarray(np.where(mask, 80, 0).astype(np.uint8), mode="L")
    radius_layer = Image.new("RGBA", base.size, (255, 255, 255, 0))
    radius_layer.putalpha(mask_img)
    overlay = Image.alpha_composite(overlay, radius_layer)

    # 绘制球场标记点
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
    frames: list, bounds: Any, mask: np.ndarray, output_dir: Path
) -> list[dict[str, str]]:
    """将每帧 CAPPI 雷达图保存为带球场标记的 PNG 文件。

    供前端时间轴播放器使用。每帧叠加分析半径、球场标记和时间标签。

    Args:
        frames: Frame 对象列表。
        bounds: Bounds 对象。
        mask: 分析半径布尔掩膜。
        output_dir: 输出目录。

    Returns:
        帧信息列表，每项包含 time / path / timestamp 字段。
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    result = []
    for i, f in enumerate(frames):
        base = f.rgba.convert("RGBA")
        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))

        # 分析半径覆盖层
        mask_img = Image.fromarray(np.where(mask, 60, 0).astype(np.uint8), mode="L")
        radius_layer = Image.new("RGBA", base.size, (255, 255, 255, 0))
        radius_layer.putalpha(mask_img)
        overlay = Image.alpha_composite(overlay, radius_layer)

        draw = ImageDraw.Draw(overlay)

        # 球场标记
        x, y = lon_lat_to_pixel(
            COURT["lon"], COURT["lat"], bounds, base.width, base.height
        )
        draw.ellipse(
            (x - 5, y - 5, x + 5, y + 5),
            fill=(255, 0, 0, 255),
            outline=(255, 255, 255, 200),
            width=2,
        )

        # 时间标签
        time_str = f.timestamp.strftime("%H:%M")
        draw.text((8, 8), time_str, fill=(255, 255, 255, 220))

        composed = Image.alpha_composite(base, overlay)
        fname = f"frame_{i:02d}.png"
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


def create_radar_contact_sheet(
    frame_entries: list[dict[str, str]],
    output_path: Path,
    max_frames: int = 6,
) -> str:
    """将最近若干帧雷达图拼接为一张缩略图（contact sheet）。

    供多模态 LLM 视觉审查使用。将最近 max_frames 帧缩小后水平排列，
    每帧上方标注时间，输出为 JPEG 格式以节省带宽。

    Args:
        frame_entries: ``save_radar_frames`` 返回的帧信息列表。
        output_path: 拼图输出路径。
        max_frames: 最多拼接的帧数，默认 6。

    Returns:
        拼图文件路径字符串，若无可用帧则返回空字符串。
    """
    selected = frame_entries[-max_frames:]
    if not selected:
        return ""

    # 加载帧图片
    images: list[tuple[dict[str, str], Image.Image]] = []
    for entry in selected:
        path = Path(entry.get("path", ""))
        if not path.exists():
            continue
        images.append((entry, Image.open(path).convert("RGBA")))
    if not images:
        return ""

    # 缩略图参数
    thumb_w = 160
    label_h = 28  # 时间标签高度
    pad = 10

    thumbs: list[tuple[dict[str, str], Image.Image]] = []
    for entry, img in images:
        ratio = thumb_w / max(1, img.width)
        thumb_h = max(1, int(img.height * ratio))
        thumb = img.resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
        thumbs.append((entry, thumb))

    # 计算画布尺寸
    cell_h = max(thumb.height for _, thumb in thumbs) + label_h
    sheet_w = pad + len(thumbs) * (thumb_w + pad)
    sheet_h = pad * 2 + cell_h

    # 深色背景画布
    sheet = Image.new("RGBA", (sheet_w, sheet_h), (12, 18, 28, 255))
    draw = ImageDraw.Draw(sheet)

    for idx, (entry, thumb) in enumerate(thumbs):
        x = pad + idx * (thumb_w + pad)
        y = pad + label_h
        # 边框
        draw.rectangle(
            (x - 1, y - label_h, x + thumb_w + 1, y + cell_h - label_h + 1),
            outline=(80, 90, 105, 255),
            width=1,
        )
        # 序号 + 时间标签
        draw.text(
            (x + 8, pad + 7),
            f"{idx + 1}. {entry.get('time', '--:--')}",
            fill=(230, 235, 245, 255),
        )
        # 贴上缩略图
        sheet.alpha_composite(thumb, (x, y))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.convert("RGB").save(output_path, quality=75)
    return str(output_path)
