"""Mini Agent Python — CLI 交互式命令行模块

提供终端交互界面，支持：
- 自然语言对话（启动 Agent 循环）
- 内置命令（.stats, .skills, .sessions, .profile 等）
- 彩色输出和实时进度指示

入口函数：main()
"""

from src.cli.cli import main

__all__ = ["main"]
