"""Mini Agent Python — CLI 输出管理器

在 CLI 交互环境中，管理 Agent 输出与用户输入之间的显示冲突。
当 Agent 执行工具或调用 LLM 时，输出可能覆盖用户的输入行。

使用方式：
    1. 异步操作前：调用 begin_output() 清除 prompt 行
    2. 执行输出：通过 write/write_lines 打印内容
    3. 异步操作后：调用 end_output() 重绘 prompt
"""

from __future__ import annotations

import sys


class OutputManager:
    """CLI 输出管理器

    解决 readline/input() 与异步输出之间的冲突。
    支持嵌套调用（计数器模式），只有最外层才会清除/重绘 prompt。

    Example:
        om = OutputManager(prompt="> ")
        om.begin_output()
        print("工具执行中...")
        om.end_output()

        # 或者使用便捷方法
        om.write("这是一行输出")
        om.write_lines(["第一行", "第二行"])
    """

    def __init__(self, prompt: str = "> ") -> None:
        """创建输出管理器

        Args:
            prompt: Prompt 字符串，用于重绘（默认 "> "）
        """
        self._prompt = prompt
        self._in_output = False
        self._output_depth = 0

    def begin_output(self) -> None:
        """开始输出阶段：清除当前行

        应在任何异步操作（工具执行、LLM 调用等）输出日志前调用。
        支持嵌套调用（计数器模式），只有第一次调用会清除行。
        """
        if self._output_depth == 0:
            # 清除当前行
            sys.stdout.write("\r" + " " * 80 + "\r")
            sys.stdout.flush()
            self._in_output = True
        self._output_depth += 1

    def end_output(self) -> None:
        """结束输出阶段：重绘 prompt

        与 begin_output() 配对使用。
        只有当计数器归零时才重绘 prompt。
        """
        if self._output_depth > 0:
            self._output_depth -= 1
        if self._output_depth == 0 and self._in_output:
            # 重绘 prompt
            sys.stdout.write(self._prompt)
            sys.stdout.flush()
            self._in_output = False

    def write(self, text: str) -> None:
        """安全输出单行文本

        自动包裹 begin_output/end_output，适合单次输出。

        Args:
            text: 要输出的文本
        """
        self.begin_output()
        print(text)
        self.end_output()

    def write_lines(self, lines: list[str]) -> None:
        """安全输出多行文本

        Args:
            lines: 要输出的文本行列表
        """
        self.begin_output()
        for line in lines:
            print(line)
        self.end_output()


__all__ = ["OutputManager"]
