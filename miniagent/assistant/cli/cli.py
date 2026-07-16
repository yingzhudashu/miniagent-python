"""Mini Agent Python — 打包 CLI 入口

``pip install`` 后的 ``miniagent`` 命令及 ``python -m miniagent.assistant.cli.cli`` 均指向本模块。
实际参数解析与启动逻辑由 ``miniagent.assistant.run_assistant`` 统一拥有。

用法（与 ``python -m miniagent`` 等价）::

    miniagent                            # 交互模式（默认）
    miniagent --help                     # 显示命令行用法
    python -m miniagent.assistant.cli.cli --help   # 同上

推荐使用 ``python -m miniagent`` 或已安装的 ``miniagent`` 命令。
"""

from __future__ import annotations


def main() -> None:
    """CLI 打包入口，委托 ``miniagent.assistant.run_assistant``。

    命令行参数通过共享的 ``sys.argv`` 传递（本函数不显式接收或转发参数）。
    """
    from miniagent.assistant import run_assistant

    run_assistant()


if __name__ == "__main__":
    main()
