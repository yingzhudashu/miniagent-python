"""Mini Agent Python — CLI 入口

提供命令行交互接口。

用法：
    python -m miniagent.cli.cli          # 交互模式
    python -m miniagent.cli.cli --help   # 显示帮助
"""

from __future__ import annotations


def main() -> None:
    """CLI 主入口。

    委托给 ``miniagent.__main__`` 的统一入口处理。
    """
    # 将 CLI 参数传递给主入口
    from miniagent.__main__ import main as entry_main

    entry_main()


if __name__ == "__main__":
    main()
