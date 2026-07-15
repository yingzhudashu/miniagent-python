"""CLI 下将 Assistant 的 Markdown 回复渲染为终端样式（可选 Rich）。

依赖 ``pip install -e ".[cli]"``。关闭 Rich 渲染、保留原始 Markdown：

- 环境变量 ``MINIAGENT_CLI_RAW_MARKDOWN=1``
- 或 ``config.user.json`` 中 ``cli.raw_markdown: true``

重要：Rich Markdown 标题默认居中（Heading 类硬编码 text.justify = "center"），
本模块通过自定义渲染强制 ATX 标题（``# `` 前缀）左对齐；fenced code block 内的
``#`` 行不会被误判为标题。Setext 标题（``===`` / ``---``）仍由 Rich 默认渲染。

**性能优化**：
- 模块级共享 Console 实例，避免重复创建
- 渲染结果缓存（LRU），避免相同内容重复渲染
"""

from __future__ import annotations

import hashlib
import re
import threading
from collections import OrderedDict
from io import StringIO
from typing import Any

from miniagent.agent.constants import CLI_RAW_MARKDOWN, CLI_RENDER_CACHE_MAX_SIZE

_STRIP_ANSI = re.compile(r"\x1b\[[0-9;]*m")

# ATX 标题：行首 # 后必须有空格
_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$")
_FENCE_LINE = re.compile(r"^(`{3,}|~{3,})(.*)$")

# ── 性能优化：模块级共享 Console ──
_shared_console_cache: OrderedDict[int, Any] = OrderedDict()  # width -> Console
_shared_console_original_file: dict[int, Any] = {}  # width -> original file
_CONSOLE_CACHE_MAX_SIZE = 16

# ── 性能优化：渲染结果缓存 ──
_RENDER_CACHE_MAX_SIZE = CLI_RENDER_CACHE_MAX_SIZE
_render_cache: OrderedDict[tuple[str, int, str], str] = OrderedDict()
_RENDER_CACHE_MAX_BYTES = 8 * 1024 * 1024
_render_cache_bytes = 0
_RENDER_LOCK = threading.RLock()


def _get_cached_console(width: int) -> Any | None:
    """获取指定宽度的缓存 Console 实例。

    Args:
        width: 渲染宽度

    Returns:
        Console 实例，或 None（Rich 未安装）
    """
    try:
        from rich.console import Console
    except ImportError:
        return None

    w = max(20, int(width))
    with _RENDER_LOCK:
        if w not in _shared_console_cache:
            console = Console(
                width=w,
                force_terminal=True,
                color_system="standard",
                highlight=False,
            )
            _shared_console_cache[w] = console
            _shared_console_original_file[w] = console.file
            while len(_shared_console_cache) > _CONSOLE_CACHE_MAX_SIZE:
                evicted_width, _console = _shared_console_cache.popitem(last=False)
                _shared_console_original_file.pop(evicted_width, None)
        else:
            _shared_console_cache.move_to_end(w)
        return _shared_console_cache[w]


def _get_render_cache_key(markdown: str, width: int, justify: str) -> tuple[str, int, str]:
    """生成渲染缓存键（SHA-256 摘要，避免内容特征冲突）。"""
    digest = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    return (digest, max(20, int(width)), justify)


def _get_cached_render(cache_key: tuple[str, int, str]) -> str | None:
    """从 LRU 缓存获取渲染结果。"""
    with _RENDER_LOCK:
        if cache_key in _render_cache:
            _render_cache.move_to_end(cache_key)
            return _render_cache[cache_key]
        return None


def _cache_render(cache_key: tuple[str, int, str], result: str) -> None:
    """写入 LRU 渲染缓存。"""
    global _render_cache_bytes
    with _RENDER_LOCK:
        old = _render_cache.pop(cache_key, None)
        if old is not None:
            _render_cache_bytes -= len(old.encode("utf-8", errors="replace"))
        _render_cache[cache_key] = result
        _render_cache_bytes += len(result.encode("utf-8", errors="replace"))
        while (
            len(_render_cache) > _RENDER_CACHE_MAX_SIZE
            or _render_cache_bytes > _RENDER_CACHE_MAX_BYTES
        ):
            _key, evicted = _render_cache.popitem(last=False)
            _render_cache_bytes -= len(evicted.encode("utf-8", errors="replace"))


def _compute_fence_mask(lines: list[str]) -> list[bool]:
    """标记 fenced code block 内部行（不含开/闭围栏行本身）。"""
    in_fence = False
    fence_char = ""
    fence_len = 0
    mask = [False] * len(lines)

    for idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            if in_fence:
                mask[idx] = True
            continue

        m = _FENCE_LINE.match(stripped)
        if m:
            marker = m.group(1)
            tail = m.group(2).strip()
            if in_fence:
                if marker[0] == fence_char and len(marker) >= fence_len:
                    in_fence = False
                    fence_char = ""
                    fence_len = 0
                else:
                    mask[idx] = True
            elif not tail or tail[0].isalnum() or tail[0] in {"_", "-", ".", "+"}:
                in_fence = True
                fence_char = marker[0]
                fence_len = len(marker)
            continue

        if in_fence:
            mask[idx] = True

    return mask


def _is_atx_heading_line(line: str, *, in_fence: bool) -> bool:
    """行是否为 ATX 标题（fenced code block 内一律否）。"""
    if in_fence:
        return False
    return _HEADING_PATTERN.match(line) is not None


def cli_raw_markdown_enabled() -> bool:
    """是否关闭 Rich 渲染、保留原始 Markdown。

    优先级：``MINIAGENT_CLI_RAW_MARKDOWN`` 环境变量 > ``cli.raw_markdown`` 配置 >
    Internal 默认值 ``CLI_RAW_MARKDOWN``。
    """
    from miniagent.assistant.infrastructure.env_parse import env_flag
    from miniagent.assistant.infrastructure.json_config import get_config

    default = bool(get_config("cli.raw_markdown", CLI_RAW_MARKDOWN))
    return env_flag("MINIAGENT_CLI_RAW_MARKDOWN", default=default)


def render_markdown_to_ansi(markdown: str, *, width: int, justify: str = "left") -> str | None:
    """将 Markdown 转为带 ANSI 序列的文本；ATX 标题强制左对齐。

    Rich Markdown 的 Heading 类硬编码 text.justify = "center"，忽略父级 justify 参数。
    本函数通过分段渲染解决：围栏外的 ATX 标题单独渲染（左对齐），其余块用 Rich Markdown。

    **性能优化**：
    - 使用缓存 Console 实例
    - LRU 缓存渲染结果

    Args:
        markdown: Markdown 文本
        width: 渲染宽度
        justify: 正文对齐方式（ATX 标题强制左对齐）

    Returns:
        ANSI 格式文本，或 None（Rich 未安装或 raw markdown 模式）
    """
    if cli_raw_markdown_enabled():
        return None

    cache_key = _get_render_cache_key(markdown, width, justify)
    cached = _get_cached_render(cache_key)
    if cached is not None:
        return cached

    try:
        from rich.markdown import Markdown
    except ImportError:
        return None

    w = max(20, int(width))
    lines = markdown.split("\n")
    fence_mask = _compute_fence_mask(lines)
    output_parts: list[str] = []

    shared_console = _get_cached_console(w)
    if shared_console is None:
        return None

    with _RENDER_LOCK:
        original_file = _shared_console_original_file.get(w)

    i = 0
    while i < len(lines):
        line = lines[i]

        if _is_atx_heading_line(line, in_fence=fence_mask[i]):
            heading_match = _HEADING_PATTERN.match(line)
            assert heading_match is not None
            level = len(heading_match.group(1))
            text = heading_match.group(2).strip()
            output_parts.append(_render_heading_left_aligned(level, text, width=w))
            i += 1
            continue

        block_lines: list[str] = []
        while i < len(lines) and not _is_atx_heading_line(lines[i], in_fence=fence_mask[i]):
            block_lines.append(lines[i])
            i += 1

        block = "\n".join(block_lines)
        if block.strip():
            buf = StringIO()
            with _RENDER_LOCK:
                shared_console.file = buf
                try:
                    shared_console.print(Markdown(block, justify=justify))
                finally:
                    shared_console.file = original_file
            output_parts.append(buf.getvalue())

    result = "".join(output_parts)
    _cache_render(cache_key, result)
    return result


def _render_heading_left_aligned(level: int, text: str, width: int) -> str:
    """渲染 ATX 标题（强制左对齐）。

    Args:
        level: 标题级别（1-6）
        text: 标题文本
        width: 渲染宽度

    Returns:
        ANSI 格式标题文本
    """
    try:
        from rich import box
        from rich.console import Console
        from rich.panel import Panel
        from rich.style import Style
        from rich.text import Text
    except ImportError:
        prefix = "#" * level
        return f"{prefix} {text}\n"

    buf = StringIO()
    console = Console(
        file=buf,
        width=width,
        force_terminal=True,
        color_system="standard",
        highlight=False,
    )

    heading_text = Text(text, justify="left")

    if level == 1:
        panel = Panel(
            heading_text,
            box=box.HEAVY,
            style=Style(color="bright_blue", bold=True),
            expand=False,
        )
        console.print(panel)
    elif level == 2:
        heading_text.stylize(Style(bold=True, underline=True, color="bright_green"))
        console.print("")
        console.print(heading_text)
        console.print("")
    elif level == 3:
        heading_text.stylize(Style(bold=True, color="yellow"))
        console.print(heading_text)
    elif level == 4:
        heading_text.stylize(Style(bold=True, dim=True))
        console.print(heading_text)
    elif level == 5:
        heading_text.stylize(Style(dim=True, italic=True))
        console.print(heading_text)
    else:
        heading_text.stylize(Style(dim=True))
        console.print(heading_text)

    return buf.getvalue()


def strip_ansi(text: str) -> str:
    """去掉 Rich 常用的 SGR 颜色转义（``\\x1b[…m``），用于剪贴板等纯文本场景。"""
    return _STRIP_ANSI.sub("", text)


__all__ = [
    "cli_raw_markdown_enabled",
    "render_markdown_to_ansi",
    "strip_ansi",
]
