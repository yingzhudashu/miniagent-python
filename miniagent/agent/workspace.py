"""Workspace resolution policy supplied through the agent settings port."""

from __future__ import annotations

import os

from miniagent.agent.settings import get_config


def get_default_workspace() -> str:
    """返回当前 Agent 配置的工作目录，未配置时使用进程 cwd。"""
    configured = get_config("paths.workspace", None)
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    return os.getcwd()


__all__ = ["get_default_workspace"]
