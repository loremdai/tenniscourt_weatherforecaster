"""项目公共工具函数。

提供跨模块复用的基础设施函数，避免在多个入口文件中重复实现。
"""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: str = ".env") -> None:
    """从 .env 文件中加载 key=value 键值对到 os.environ。

    轻量级自实现 dotenv 加载器，避免引入 python-dotenv 第三方依赖。

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
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value
