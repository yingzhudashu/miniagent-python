"""测试用 JSON 配置辅助（替代 MINIAGENT_* 环境变量覆盖）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULTS_PATH = PROJECT_ROOT / "config.defaults.json"


def install_test_config(
    tmp_path: Path,
    overrides: dict[str, Any] | None = None,
    *,
    user_path: Path | None = None,
) -> None:
    """安装隔离 JsonConfigLoader（defaults + 可选 user 覆盖）。"""
    from miniagent.infrastructure.json_config import JsonConfigLoader

    if user_path is None:
        user_path = tmp_path / "config.user.json"
        user_path.write_text(json.dumps(overrides or {}), encoding="utf-8")

    JsonConfigLoader._instance = JsonConfigLoader(
        defaults_path=str(DEFAULTS_PATH),
        user_path=str(user_path),
    )
    JsonConfigLoader.get_instance().reload()


def deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out
