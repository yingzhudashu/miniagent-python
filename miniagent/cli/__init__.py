"""CLI 子包 — ``console_scripts`` 入口 ``miniagent`` 指向 ``cli.main``。

``main`` 委托 ``miniagent.__main__.main`` 处理参数并启动正式应用入口。

与 ``pyproject.toml`` 中 ``[project.scripts] miniagent = miniagent.cli.cli:main`` 一致。
"""

from __future__ import annotations

__all__ = ["main"]


def __getattr__(name: str) -> object:
    if name == "main":
        from miniagent.cli.cli import main

        return main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
