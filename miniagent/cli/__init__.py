"""CLI 子包 — ``console_scripts`` 入口 ``miniagent`` 指向 ``cli.main``。

``main`` 委托 ``miniagent.__main__.main``（加载 ``.env``、``--stop``、``unified_entry``）。"""

from miniagent.cli.cli import main

__all__ = ["main"]
