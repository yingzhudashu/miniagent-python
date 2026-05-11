"""CLI 子包 — ``console_scripts`` 入口 ``miniagent`` 指向 ``cli.main``。

``main`` 委托 ``miniagent.__main__.main``（加载 ``.env``、``--stop``、``unified_entry``）。

与 ``pyproject.toml`` 中 ``[project.scripts] miniagent = miniagent.cli.cli:main`` 一致。
"""

from miniagent.cli.cli import main

__all__ = ["main"]
