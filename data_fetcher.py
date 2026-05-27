"""数据获取层。

负责所有与外部 API 的网络交互和本地缓存管理：
    - 从广东气象局 GD121 主 API 拉取 CAPPI 雷达 + QPF 定量降水预报
    - 从格点实况 API (ra.gd121.cn) 拉取精细化插值气象数据
    - 下载并缓存 CAPPI 雷达帧 PNG 图片
    - 清理过期的本地缓存文件，避免磁盘占用无限增长

设计原则：
    - 纯 I/O 操作，不包含任何图像处理或业务逻辑
    - 所有 API 配置（URL 模板、请求头）统一从 config.py 导入
    - 失败时尽量返回 None 或静默处理，让上游决定回退策略
"""

from __future__ import annotations

import json
import ssl
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from config import (
    API_URL_TEMPLATE,
    API_HEADERS,
    RA_API_URL_TEMPLATE,
    RA_API_HEADERS,
)


# ═══════════════════════════════════════════════════════════════════════════════
# API 数据拉取
# ═══════════════════════════════════════════════════════════════════════════════


def fetch_weather_data(lon: float, lat: float, timeout: float = 30) -> dict[str, Any]:
    """从 GD121 主 API 拉取天气数据。

    返回包含 CAPPI 雷达帧列表、QPF 定量降水预报、气象站实况等字段的
    完整 JSON 响应。这是整个系统的主数据源。

    Args:
        lon: 查询经度（WGS-84）。
        lat: 查询纬度（WGS-84）。
        timeout: 请求超时时间（秒），默认 30 秒。

    Returns:
        解析后的 JSON 字典。

    Raises:
        urllib.error.URLError: 网络不可达或超时。
        json.JSONDecodeError: 响应不是合法 JSON。
    """
    url = API_URL_TEMPLATE.format(lon=lon, lat=lat)

    # 跳过 SSL 证书验证 —— GD121 服务器的证书偶尔会过期或不匹配
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(url, headers=API_HEADERS)
    with urllib.request.urlopen(req, context=ctx, timeout=timeout) as response:
        data = response.read().decode("utf-8")
        return json.loads(data)


def fetch_grid_weather(
    lon: float, lat: float, timeout: float = 15
) -> dict[str, Any] | None:
    """从格点实况 API 拉取精细化插值气象数据。

    与 ``fetch_weather_data`` 不同，这个 API 返回的是经纬度精确插值后的
    实时天气数据，而不是最近气象站的观测值。数据包括：
        - 实时温湿风、天气状态
        - 未来 2 小时降雨预报（rainFlag + message）
        - 逐小时预报（24h）和 7 天预报

    Args:
        lon: 查询经度。
        lat: 查询纬度。
        timeout: 请求超时时间（秒），默认 15 秒（通常比主 API 更快）。

    Returns:
        解析后的 data 字段内容，或在请求失败时返回 None。
        返回 None 时系统会降级使用主 API 的气象站数据。
    """
    url = RA_API_URL_TEMPLATE.format(lon=lon, lat=lat)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        req = urllib.request.Request(url, headers=RA_API_HEADERS)
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as response:
            raw = json.loads(response.read().decode("utf-8"))
            if raw.get("status") == 200 and "data" in raw:
                return raw["data"]
    except Exception as e:
        print(f"Warning: grid weather API failed: {e}", file=sys.stderr)
    return None


def load_response(path: Path) -> dict[str, Any]:
    """从本地文件加载 JSON 响应（离线/调试模式使用）。

    Args:
        path: JSON 文件的路径。

    Returns:
        解析后的 JSON 字典，结构与 ``fetch_weather_data`` 返回值一致。
    """
    return json.loads(path.read_text(encoding="utf-8"))


# ═══════════════════════════════════════════════════════════════════════════════
# CAPPI 帧缓存管理
# ═══════════════════════════════════════════════════════════════════════════════


def download_frame(url: str, cache_dir: Path, timeout: float = 30) -> Path:
    """下载一帧 CAPPI 雷达 PNG 图片并缓存到本地。

    若本地已存在同名且非空的文件，则直接返回路径，不重复下载。
    文件名从 URL 末尾自动提取。

    Args:
        url: CAPPI 帧图片的完整 URL。
        cache_dir: 本地缓存目录（不存在时自动创建）。
        timeout: 下载超时时间（秒）。

    Returns:
        下载后的本地文件路径。
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    # 从 URL 提取文件名，并去除 OSS 处理后缀
    filename = url.rsplit("/", 1)[-1].replace("!wbdstyle", "")
    path = cache_dir / filename

    # 命中缓存则跳过下载
    if path.exists() and path.stat().st_size > 0:
        return path

    request = urllib.request.Request(url, headers={"User-Agent": "nowcast-mvp/0.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        path.write_bytes(response.read())
    return path


def cleanup_cache(cache_dir: Path, max_age_hours: float = 0.5) -> None:
    """清理缓存目录中超过指定时间的旧文件。

    守护进程模式下每轮会下载新的 CAPPI 帧，旧帧如果不清理会无限堆积。
    此函数在每轮预报周期结束后调用，删除过期的 PNG 文件。

    Args:
        cache_dir: 缓存目录路径。
        max_age_hours: 文件最大保留时间（小时），默认 0.5 小时。
    """
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
            # 删除失败不应中断程序，仅打印警告
            print(
                f"Warning: failed to delete old cache file {file_path}: {e}",
                file=sys.stderr,
            )
