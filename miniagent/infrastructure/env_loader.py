"""加载项目根目录 ``.env``（幂等，不覆盖已存在的进程环境变量）。"""

from __future__ import annotations

import os


def load_dotenv_from_project_root() -> None:
    try:
        from dotenv import load_dotenv

        root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        env_path = os.path.join(root, ".env")
        if os.path.exists(env_path):
            load_dotenv(env_path)
    except ImportError:
        pass
