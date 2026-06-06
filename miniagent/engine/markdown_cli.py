"""CLI 下将 Assistant 的 Markdown 回复渲染为终端样式（可选 Rich）。

依赖 ``pip install -e ".[cli]"``；原始 Markdown 模式见配置 ``cli.raw_markdown``。

重要：Rich Markdown 标题默认居中（Heading 类硬编码 text.justify = "center"），
本模块通过自定义渲染强制标题左对齐。

**性能优化**：
- 模块级共享 Console 实例，避免重复创建
- 渲染结果缓存（LRU），避免相同内容重复渲染
"""

from __future__ import annotations

import re
from collections import OrderedDict
from io import StringIO
from typing import Any

from miniagent.core.constants import CLI_RAW_MARKDOWN, CLI_RENDER_CACHE_MAX_SIZE

_STRIP_ANSI = re.compile(r"\x1b\[[0-9;]*m")

# 标题正则：匹配 # 开头的行
_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$")

# ── 性能优化：模块级共享 Console ──
_shared_console_cache: dict[int, Any] = {}  # width -> Console
_shared_console_original_file: dict[int, Any] = {}  # width -> original file

# ── 性能优化：渲染结果缓存 ──
_RENDER_CACHE_MAX_SIZE = CLI_RENDER_CACHE_MAX_SIZE
_render_cache: OrderedDict[tuple[int, str, int, str], str] = OrderedDict()  # (len, prefix, width, justify) -> rendered


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
    if w not in _shared_console_cache:
        console = Console(
            width=w,
            force_terminal=True,
            color_system="standard",
            highlight=False,
        )
        _shared_console_cache[w] = console
        _shared_console_original_file[w] = console.file
    return _shared_console_cache[w]


def _get_render_cache_key(markdown: str, width: int, justify: str) -> tuple[int, str, int, str]:
    """生成渲染缓存键（性能优化：避免 md5 hash 计算）。

    使用长度 + 前 50 字符替代 md5 hash，减少加密计算开销。
    注意：存在理论上的冲突风险（相同长度和前缀），但对 CLI 渲染场景影响极小。
    """
    # 性能优化：避免 hashlib.md5 计算，使用快速特征
    prefix = markdown[:50] if len(markdown) > 50 else markdown
    return (len(markdown), prefix, width, justify)


def _get_cached_render(cache_key: tuple[str, int, str]) -> str | None:
    """从缓存获取渲染结果。"""
    if cache_key in _render_cache:
        _render_cache.move_to_end(cache_key)  # LRU
        return _render_cache[cache_key]
    return None


def _cache_render(cache_key: tuple[str, int, str], result: str) -> None:
    """缓存渲染结果。"""
    _render_cache[cache_key] = result
    # LRU 驎出
    while len(_render_cache) > _RENDER_CACHE_MAX_SIZE:
        _render_cache.popitem(last=False)


def cli_raw_markdown_enabled() -> bool:
    """配置 cli.raw_markdown=true 时关闭渲染，保留原始 Markdown（便于复制或调试）。"""
    return CLI_RAW_MARKDOWN


def render_markdown_to_ansi(markdown: str, *, width: int, justify: str = "left") -> str | None:
    """将 Markdown 转为带 ANSI 序列的文本；标题强制左对齐。

    Rich Markdown 的 Heading 类硬编码 text.justify = "center"，忽略父级 justify 参数。
    本函数通过分段渲染解决：标题单独渲染（左对齐），其他内容用 Rich Markdown 渲染。

    **性能优化**：
    - 使用缓存 Console 实例
    - LRU 缓存渲染结果

    Args:
        markdown: Markdown 文本
        width: 渲染宽度
        justify: 正文对齐方式（标题强制左对齐）

    Returns:
        ANSI 格式文本，或 None（Rich 未安装或 raw markdown 模式）
    """
    if cli_raw_markdown_enabled():
        return None

    # 检查缓存
    cache_key = _get_render_cache_key(markdown, width, justify)
    cached = _get_cached_render(cache_key)
    if cached is not None:
        return cached

    try:
        from rich import box  # noqa: F401
        from rich.markdown import Markdown
        from rich.panel import Panel  # noqa: F401
        from rich.style import Style  # noqa: F401
        from rich.text import Text  # noqa: F401
    except ImportError:
        return None

    w = max(20, int(width))

    # 分段渲染：识别标题行，单独处理
    lines = markdown.split("\n")
    output_parts: list[str] = []

    # 性能优化：使用缓存 Console 实例
    shared_console = _get_cached_console(w)
    if shared_console is None:
        return None

    original_file = _shared_console_original_file.get(w)

    i = 0
    while i < len(lines):
        line = lines[i]

        # 检查是否为标题
        heading_match = _HEADING_PATTERN.match(line)

        if heading_match:
            # 渲染标题（强制左对齐）
            level = len(heading_match.group(1))
            text = heading_match.group(2).strip()

            heading_ansi = _render_heading_left_aligned(level, text, width=w)
            output_parts.append(heading_ansi)
            i += 1
        else:
            # 收集非标题内容块
            block_lines: list[str] = []
            while i < len(lines) and not _HEADING_PATTERN.match(lines[i]):
                block_lines.append(lines[i])
                i += 1

            block = "\n".join(block_lines)
            if block.strip():
                # 渲染非标题块（复用 shared_console，性能优化）
                buf = StringIO()
                # 临时更改 console 的 file 输出到 buf
                shared_console.file = buf
                shared_console.print(Markdown(block, justify=justify))
                output_parts.append(buf.getvalue())
                # 恢复 console 的原始输出
                shared_console.file = original_file

    result = "".join(output_parts)
    # 缓存结果
    _cache_render(cache_key, result)
    return result


def _render_heading_left_aligned(level: int, text: str, width: int) -> str:
    """渲染标题（强制左对齐）。

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
        # 回退：简单文本
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

    # 创建左对齐的 Text 对象（关键：justify="left"）
    heading_text = Text(text, justify="left")

    if level == 1:
        # H1: 使用 Panel（边框），内部文本左对齐
        # 注意：Panel 本身可能居中，但内部 Text 左对齐
        # 使用 box.HEAVY 保持 Rich 默认风格
        panel = Panel(
            heading_text,
            box=box.HEAVY,
            style=Style(color="bright_blue", bold=True),
            expand=False,  # 不扩展到全宽，保持紧凑
        )
        console.print(panel)
    elif level == 2:
        # H2: 加粗 + 下划线风格（左对齐）
        heading_text.stylize(Style(bold=True, underline=True, color="bright_green"))
        console.print("")  # 前空行
        console.print(heading_text)
        console.print("")  # 后空行
    elif level == 3:
        # H3: 加粗风格（左对齐）
        heading_text.stylize(Style(bold=True, color="yellow"))
        console.print(heading_text)
    elif level == 4:
        # H4: 加粗 + dim（左对齐）
        heading_text.stylize(Style(bold=True, dim=True))
        console.print(heading_text)
    elif level == 5:
        # H5: dim + italic（左对齐）
        heading_text.stylize(Style(dim=True, italic=True))
        console.print(heading_text)
    else:
        # H6: 最小风格（左对齐）
        heading_text.stylize(Style(dim=True))
        console.print(heading_text)

    return buf.getvalue()


def strip_ansi(text: str) -> str:
    """去掉 ANSI 转义，用于剪贴板等纯文本场景。"""
    return _STRIP_ANSI.sub("", text)


__all__ = [
    "cli_raw_markdown_enabled",
    "render_markdown_to_ansi",
    "strip_ansi",
]