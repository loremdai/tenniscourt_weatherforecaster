#!/usr/bin/env python3
"""仪表盘 HTTP 服务 + API 代理。

提供两类服务：
    1. 静态文件服务（前端 HTML/CSS/JS 和 output/ 数据文件）
    2. API 路由：
       - GET  /api/search?q=关键字  — 代理高德 Input Tips（地址搜索）
       - GET  /api/location         — 返回当前分析位置
       - POST /api/location         — 切换分析位置
"""

from __future__ import annotations

import http.server
import json
import os
import socketserver
import sys
import urllib.parse
import urllib.request
from pathlib import Path


from utils import load_dotenv

# 先加载 .env，再导入 config（config 中通过 os.getenv 读取 API Key）
load_dotenv()

from config import (  # noqa: E402
    AMAP_API_KEY,
    AMAP_INPUT_TIPS_URL,
    COURT,
    DASHBOARD_PORT,
)

PORT = DASHBOARD_PORT
DIRECTORY = os.path.dirname(os.path.abspath(__file__))
RUNTIME_LOCATION_PATH = Path(DIRECTORY) / "output" / "runtime_location.json"


def _read_runtime_location() -> dict:
    """读取运行时位置配置，不存在或格式错误时返回默认 COURT。"""
    try:
        if RUNTIME_LOCATION_PATH.is_file():
            data = json.loads(
                RUNTIME_LOCATION_PATH.read_text(encoding="utf-8")
            )
            if data.get("lon") and data.get("lat"):
                return data
    except (json.JSONDecodeError, OSError):
        pass
    return {
        "id": COURT["id"],
        "name": COURT["name"],
        "lon": COURT["lon"],
        "lat": COURT["lat"],
    }


def _write_runtime_location(data: dict) -> None:
    """写入运行时位置配置。"""
    RUNTIME_LOCATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    RUNTIME_LOCATION_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


class Handler(http.server.SimpleHTTPRequestHandler):
    """扩展的 HTTP 处理器，支持 API 路由和静态文件服务。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def end_headers(self):
        # 禁用缓存，确保前端获取最新 JSON 数据
        self.send_header(
            "Cache-Control", "no-store, no-cache, must-revalidate"
        )
        return super().end_headers()

    # ---- API 路由分发 ----

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/search":
            self._handle_search(parsed)
        elif parsed.path == "/api/location":
            self._handle_get_location()
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/location":
            self._handle_set_location()
        else:
            self.send_error(404, "Not Found")

    # ---- API 实现 ----

    def _json_response(self, data: dict, status: int = 200) -> None:
        """发送 JSON 响应。"""
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_search(self, parsed: urllib.parse.ParseResult) -> None:
        """代理高德 Input Tips API，限制搜索范围为广东省。"""
        qs = urllib.parse.parse_qs(parsed.query)
        keyword = qs.get("q", [""])[0].strip()

        if not keyword:
            self._json_response({"tips": []})
            return

        if not AMAP_API_KEY:
            self._json_response(
                {"error": "AMAP_MAPS_API_KEY 未配置"}, status=500
            )
            return

        # 构造高德 API 请求，限制搜索范围为广东省（adcode: 440000）
        params = urllib.parse.urlencode({
            "key": AMAP_API_KEY,
            "keywords": keyword,
            "city": "440000",
            "citylimit": "true",
            "datatype": "all",
        })
        url = f"{AMAP_INPUT_TIPS_URL}?{params}"

        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=5) as resp:
                raw = json.loads(resp.read().decode("utf-8"))

            # 筛选有经纬度坐标的结果
            tips = []
            for tip in raw.get("tips", []):
                location = tip.get("location", "")
                if not location or not isinstance(location, str):
                    continue
                parts = location.split(",")
                if len(parts) != 2:
                    continue
                try:
                    lon, lat = float(parts[0]), float(parts[1])
                except (ValueError, TypeError):
                    continue
                tips.append({
                    "name": tip.get("name", ""),
                    "district": tip.get("district", ""),
                    "address": tip.get("address", ""),
                    "lon": lon,
                    "lat": lat,
                })

            self._json_response({"tips": tips})

        except Exception as e:
            self._json_response(
                {"error": f"高德 API 请求失败: {e}"}, status=502
            )

    def _handle_get_location(self) -> None:
        """返回当前分析位置。"""
        self._json_response(_read_runtime_location())

    def _handle_set_location(self) -> None:
        """接收前端提交的新位置并保存。"""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            data = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, ValueError):
            self._json_response({"error": "无效的 JSON"}, status=400)
            return

        lon = data.get("lon")
        lat = data.get("lat")
        name = data.get("name", "未知位置")

        if not isinstance(lon, (int, float)) or not isinstance(
            lat, (int, float)
        ):
            self._json_response(
                {"error": "缺少有效的 lon/lat"}, status=400
            )
            return

        location = {
            "id": "user_selected",
            "name": name,
            "lon": round(lon, 6),
            "lat": round(lat, 6),
        }
        _write_runtime_location(location)

        # Write intermediate forecast and diagnosis immediately so that frontend
        # sees the new location and skeleton loader instantly.
        forecast_path = Path(DIRECTORY) / "output" / "forecast.json"
        diagnosis_path = Path(DIRECTORY) / "output" / "diagnosis.json"
        try:
            forecast_path.parent.mkdir(parents=True, exist_ok=True)
            forecast_path.write_text(
                json.dumps({
                    "llm_generating": True,
                    "court": location
                }, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8"
            )
            diagnosis_path.write_text(
                json.dumps({
                    "llm_generating": True
                }, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8"
            )
        except Exception as e:
            print(f"Warning: failed to write intermediate files in API: {e}", file=sys.stderr)

        self._json_response({"ok": True, "location": location})


def main():
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(
            f"Serving dashboard at http://localhost:{PORT}/frontend/index.html"
        )
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nStopping server")
            sys.exit(0)


if __name__ == "__main__":
    main()
