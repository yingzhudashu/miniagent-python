"""路径解析辅助（单一事实来源）。"""

from __future__ import annotations

import os

from miniagent.infrastructure.json_config import get_config


def resolve_state_dir() -> str:
    """解析运行时状态根目录。

    ``paths.state_dir`` 为相对路径时基于当前工作目录；缺省为 ``workspaces``。
    """
    raw = get_config("paths.state_dir", "workspaces")
    if not raw or not str(raw).strip():
        return os.path.join(os.getcwd(), "workspaces")
    path = str(raw).strip()
    if os.path.isabs(path):
        return path
    return os.path.join(os.getcwd(), path)


__all__ = ["resolve_state_dir"]
