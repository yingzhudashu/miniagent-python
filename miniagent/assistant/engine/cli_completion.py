"""全屏 CLI 的命令与文件路径补全器。

该适配模块依赖可选的 ``prompt_toolkit``，因此只由全屏 TUI 的可选依赖分支
导入。补全器不读取运行时全局状态；命令清单由调用方显式传入。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

from prompt_toolkit.completion import Completer, Completion, PathCompleter, merge_completers
from prompt_toolkit.document import Document

_logger = logging.getLogger(__name__)


class CommandCompleter(Completer):
    """对注册表提供的斜杠命令做大小写不敏感的前缀补全。"""

    def __init__(self, command_names: Iterable[str]) -> None:
        """冻结命令清单，避免补全过程中观察到半更新状态。"""
        self._command_names = tuple(command_names)

    def get_completions(self, document, complete_event):
        """仅补全光标前第一个、以 ``/`` 开头的命令 token。"""
        del complete_event
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        parts = text.split()
        if not parts:
            return
        prefix = parts[0].lower()
        for command in self._command_names:
            if command.lower().startswith(prefix):
                yield Completion(
                    command,
                    start_position=-len(prefix),
                    display=command,
                    display_meta="命令",
                )


class FileMarkerCompleter(Completer):
    """补全 ``@file:`` 和 ``file:`` 标记后的本地路径。"""

    _MARKER_PATTERN = re.compile(r"(@file:|file:)([^\s]*)$")

    def __init__(self, path_completer: Completer | None = None) -> None:
        """允许测试或平台适配器注入路径补全实现。"""
        self._path_completer = path_completer or PathCompleter()

    def get_completions(self, document, complete_event):
        """把标记后的部分作为独立 Document 交给路径补全器。"""
        match = self._MARKER_PATTERN.search(document.text_before_cursor)
        if not match:
            return
        partial_path = match.group(2)
        path_document = Document(partial_path, cursor_position=len(partial_path))
        try:
            completions = self._path_completer.get_completions(path_document, complete_event)
            for completion in completions:
                yield Completion(
                    completion.text,
                    start_position=-len(partial_path),
                    display=completion.display,
                    display_meta="文件",
                )
        except (OSError, ValueError) as exc:
            _logger.debug("文件路径补全失败: %s", exc)


def create_cli_completer(command_names: Iterable[str]) -> Completer:
    """组合命令补全与文件标记补全，供输入 Buffer 使用。"""
    return merge_completers([CommandCompleter(command_names), FileMarkerCompleter()])


__all__ = ["CommandCompleter", "FileMarkerCompleter", "create_cli_completer"]
