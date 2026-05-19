#!/usr/bin/env python3
"""网球场天气决策系统的统一入口模块。

本模块负责：
    1. 加载 .env 环境变量
    2. 解析命令行参数（CLI）
    3. 根据运行模式（单次 / 守护进程）调度预报管线

用法示例：
    # 即时短临预报（默认 target-time 为 "now"）
    python3 main.py

    # 今晚 8 点开场、预计打 2 小时的预约决策
    python3 main.py --target-time 20:00 --play-duration 120

    # 守护进程模式：每 6 分钟自动刷新一次
    python3 main.py --daemon --interval 360 --target-time 20:00

    # 跳过大模型诊断（更快，仅使用规则引擎）
    python3 main.py --target-time 20:00 --no-llm
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path


# ═══════════════════════════════════════════════════════════════════════════════
# 环境变量加载
# ═══════════════════════════════════════════════════════════════════════════════


def _load_dotenv(path: str = ".env") -> None:
    """从 .env 文件中加载 key=value 键值对到 os.environ。

    此函数是一个轻量级的自实现 dotenv 加载器，避免引入 python-dotenv
    第三方依赖。

    规则：
        - 忽略空行和以 '#' 开头的注释行
        - 忽略不含 '=' 的行
        - 自动去除 key/value 两端的空白，以及 value 外层的引号
        - 不会覆盖系统已有的同名环境变量（优先级：系统 > .env）

    Args:
        path: .env 文件的路径，默认为项目根目录下的 ".env"。
    """
    env_path = Path(path)
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        # 跳过空行和注释行
        if not line or line.startswith("#"):
            continue
        # 跳过不含 '=' 的无效行
        if "=" not in line:
            continue
        # 按第一个 '=' 拆分为 key 和 value
        key, _, value = line.partition("=")
        key = key.strip()
        # 去除 value 外层可能存在的单引号或双引号
        value = value.strip().strip("'\"")
        # 仅在系统中尚未设置该变量时才写入（不覆盖已有值）
        if key and key not in os.environ:
            os.environ[key] = value


# 在模块导入阶段立即执行 dotenv 加载，
# 确保后续导入的模块（如 llm_service）在初始化时就能读到环境变量。
_load_dotenv()

# 本地模块 —— 必须在 dotenv 加载之后再导入，
# 因为 llm_service 初始化时会读取 Langfuse 相关的环境变量。
from llm_service import flush_langfuse  # noqa: E402
from pipeline import run_once  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════════
# 命令行参数解析
# ═══════════════════════════════════════════════════════════════════════════════


def parse_args() -> argparse.Namespace:
    """解析命令行参数并返回 Namespace 对象。

    参数分为以下五组：
        - 数据源：控制离线 / 在线模式和刷新频率
        - 预约设置：目标开场时间和打球时长
        - 输出路径：预报报告、诊断结果、调试图片等
        - 网络设置：数据接口的超时时间
        - LLM 设置：大模型相关开关、API key、超时和雷达视觉审查模式

    Returns:
        argparse.Namespace: 包含所有已解析参数的命名空间对象。
    """
    parser = argparse.ArgumentParser(
        description="Tennis court weather decision system",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # ---- 数据源参数 ----
    parser.add_argument(
        "--response",
        default="response.txt",
        help="本地 JSON 响应文件路径（离线模式使用）。",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="启用守护进程模式，从 API 获取实时数据（在线模式）。",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=360,
        help="守护进程模式下两次刷新之间的间隔秒数（默认: 360）。",
    )

    # ---- 预约设置参数 ----
    parser.add_argument(
        "--target-time",
        default="now",
        help="预约开场时间，格式为 HH:MM 或 'now'（默认: now）。",
    )
    parser.add_argument(
        "--play-duration",
        type=int,
        default=120,
        help="预计打球时长，单位为分钟（默认: 120）。",
    )

    # ---- 输出路径参数 ----
    parser.add_argument(
        "--output",
        default="output/forecast.json",
        help="预报 JSON 报告的输出路径。",
    )
    parser.add_argument(
        "--diagnosis-output",
        default="output/diagnosis.json",
        help="LLM 诊断结果的 JSON 输出路径。",
    )
    parser.add_argument(
        "--debug-image",
        default="output/debug_court_radius.png",
        help="调试图片路径。设为空字符串 '' 可跳过生成。",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=12,
        help="使用的最大 CAPPI 雷达帧数（默认: 12）。",
    )
    parser.add_argument(
        "--network-timeout",
        type=float,
        default=30.0,
        help="每个网络/API 请求的超时时间，单位为秒（默认: 30）。",
    )

    # ---- LLM 相关参数 ----
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="跳过大模型诊断，仅使用规则引擎进行决策。",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="DashScope API key（默认从环境变量 DASHSCOPE_API_KEY 读取）。",
    )
    parser.add_argument(
        "--llm-timeout",
        type=float,
        default=45.0,
        help="每次 LLM API 读写操作的超时时间，单位为秒（默认: 45）。",
    )
    parser.add_argument(
        "--radar-vision",
        choices=("off", "auto", "always"),
        default=os.getenv("RADAR_VISION_MODE", "off"),
        help=(
            "雷达多模态视觉审查模式："
            "off=从不调用, auto=仅在雷达与官方数据冲突时调用, "
            "always=每轮都调用（默认: 环境变量 RADAR_VISION_MODE 或 off）。"
        ),
    )

    args = parser.parse_args()

    # 校验 radar_vision 参数值的合法性
    if args.radar_vision not in {"off", "auto", "always"}:
        print(
            f"Warning: invalid RADAR_VISION_MODE={args.radar_vision!r}; using 'off'.",
            file=sys.stderr,
        )
        args.radar_vision = "off"
    return args


# ═══════════════════════════════════════════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════════════════════════════════════════


def main() -> int:
    """程序主函数，负责根据运行模式调度预报管线。

    运行模式：
        - 单次模式（默认）：执行一次完整的预报周期后退出。
        - 守护进程模式（--daemon）：无限循环执行预报周期，
          每次间隔 --interval 秒。单次执行失败不会中断循环。

    每个周期结束后都会调用 flush_langfuse() 刷新可观测性追踪数据。

    Returns:
        int: 退出码，0 表示正常退出。
    """
    args = parse_args()

    if args.daemon:
        # ---- 守护进程模式：循环执行预报周期 ----
        target_label = args.target_time if args.target_time != "now" else "实时"
        print(f"Starting daemon. Target: {target_label}, Interval: {args.interval}s")
        while True:
            try:
                run_once(args)
            except Exception as e:
                # 单次执行失败仅打印错误，不中断守护进程
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"[{ts}] Error: {e}", file=sys.stderr)
            # 每轮结束后刷新 Langfuse 追踪数据，确保及时上传
            flush_langfuse()
            print(f"Waiting {args.interval} seconds...")
            time.sleep(args.interval)
    else:
        # ---- 单次模式：执行一次后退出 ----
        run_once(args)
        flush_langfuse()
    return 0


# ═══════════════════════════════════════════════════════════════════════════════
# 脚本入口
# ═══════════════════════════════════════════════════════════════════════════════


if __name__ == "__main__":
    try:
        # 通过 SystemExit 将 main() 的返回值作为进程退出码
        raise SystemExit(main())
    except Exception as exc:
        # 兜底异常处理：打印错误并以非零状态退出
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
