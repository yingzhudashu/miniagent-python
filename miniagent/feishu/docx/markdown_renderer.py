"""Markdown → 飞书文档块完整渲染器。

使用 mistune 解析 Markdown AST，转换为飞书文档块结构。
支持：标题、段落、列表、代码块、引用、表格、图片、内联样式。

两种渲染路径：
- markdown.py: 保守剥离，只输出纯文本块
- markdown_renderer.py: 完整渲染，保留结构和样式

- ``markdown_to_plain_text`` / ``markdown_to_blocks`` 生成纯文本块
- ``append_markdown_to_document`` 可显式选择富文本或纯文本渲染

mistune 3.x API 说明：
- mistune.create_markdown(renderer=None) 返回 token AST（dict 列表）
- 每个 token 包含 'type', 'attrs', 'children' 等字段
- 需要遍历 AST 并手动转换为 FeishuBlock

依赖说明：
- mistune>=3.0.0 是可选依赖（feishu extra）
- 未安装时，富文本渲染会回退到纯文本模式
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

# 可选导入：mistune 是 feishu extra 的可选依赖
try:
    import mistune
    _HAS_MISTUNE = True
except ImportError:
    mistune = None  # type: ignore
    _HAS_MISTUNE = False

_logger = logging.getLogger("miniagent.feishu.markdown_renderer")

# === 数据结构 ===


class BlockType(IntEnum):
    """飞书文档块类型常量（与飞书 API 一致）"""

    PAGE = 1  # 页面块（根容器）
    TEXT = 2  # 段落/文本块
    HEADING1 = 3  # 一级标题
    HEADING2 = 4  # 二级标题
    HEADING3 = 5  # 三级标题
    HEADING4 = 6  # 四级标题
    HEADING5 = 7  # 五级标题
    HEADING6 = 8  # 六级标题
    BULLET = 9  # 无序列表项
    ORDERED = 10  # 有序顺表项
    CODE = 11  # 代码块
    QUOTE = 12  # 引用块
    TABLE = 13  # 表格块
    IMAGE = 14  # 图片块


@dataclass
class TextStyle:
    """text_run 的样式属性"""

    bold: bool = False
    italic: bool = False
    underline: bool = False
    strikethrough: bool = False
    inline_code: bool = False
    link: str | None = None  # URL


@dataclass
class TextRun:
    """文本片段（带样式）"""

    content: str
    style: TextStyle = field(default_factory=TextStyle)


@dataclass
class FeishuBlock:
    """飞书块的中间表示（用于转换到 lark-oapi Block）"""

    block_type: BlockType
    text_runs: list[TextRun] = field(default_factory=list)
    # 特殊块属性
    code_language: str | None = None  # 代码块语言标记
    indent_level: int = 0  # 列表缩进层级（0-8）
    table_data: list[list[TextRun]] | None = None  # 表格单元格（二维数组）
    image_token: str | None = None  # 图片 token（需上传后获取）
    image_url: str | None = None  # 图片原始 URL（未上传时记录）


@dataclass
class MarkdownConversionResult:
    """Markdown 转换结果"""

    blocks: list[FeishuBlock] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # 统计信息
    stats: dict[str, int] = field(default_factory=dict)


# === 辅助函数 ===


def _chunk_text(text: str, max_chars: int = 1800) -> list[str]:
    """将长文本分片（飞书 text_run 单次最多 1800 字符）"""
    if not text:
        return ["​"]  # 零宽空格，避免空内容
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    s = text
    while s:
        chunks.append(s[:max_chars])
        s = s[max_chars:]
    return chunks


def _runs_to_text(runs: list[TextRun]) -> str:
    """将 TextRun 列表合并为纯文本"""
    return "".join(r.content for r in runs)


def _plain_runs(runs: list[TextRun], *, prefix: str = "") -> list[TextRun]:
    """Drop inline SDK styles that are frequently rejected by Docx validation."""
    text = prefix + _runs_to_text(runs)
    return [TextRun(text or "\u200b")]


def _merge_runs_with_style(runs: list[TextRun]) -> list[TextRun]:
    """合并相同样式的连续 TextRun（优化）"""
    if not runs:
        return []
    result: list[TextRun] = []
    current = runs[0]
    for next_run in runs[1:]:
        # 如果样式相同，合并内容
        if current.style == next_run.style:
            current = TextRun(current.content + next_run.content, current.style)
        else:
            result.append(current)
            current = next_run
    result.append(current)
    return result


# === AST 遍历渲染器 ===


class FeishuASTRenderer:
    """遍历 mistune AST token，转换为 FeishuBlock。

    mistune 3.x 返回的 AST 结构示例：
    [
        {'type': 'heading', 'attrs': {'level': 1}, 'children': [{'type': 'text', 'raw': 'Title'}]},
        {'type': 'paragraph', 'children': [{'type': 'text', 'raw': 'Hello'}]},
    ]
    """

    def __init__(self, *, handle_images: bool = False):
        """初始化渲染器。

        Args:
            handle_images: 是否处理图片（需要上传逻辑）
        """
        self.handle_images = handle_images
        self.warnings: list[str] = []
        self.indent_level = 0  # 列表嵌套层级

    def render(self, tokens: list[dict]) -> list[FeishuBlock]:
        """渲染 token AST 为 FeishuBlock 列表。"""
        blocks: list[FeishuBlock] = []
        for token in tokens:
            block = self._render_token(token)
            if block is None:
                continue
            if isinstance(block, list):
                blocks.extend(block)
            elif isinstance(block, FeishuBlock):
                blocks.append(block)
        return blocks

    def _render_token(self, token: dict) -> FeishuBlock | list[FeishuBlock] | None:
        """渲染单个 token。"""
        token_type = token.get("type", "")

        # 块级元素
        if token_type == "heading":
            return self._render_heading(token)
        elif token_type == "paragraph":
            return self._render_paragraph(token)
        elif token_type == "list":
            return self._render_list(token)
        elif token_type == "block_code":
            return self._render_block_code(token)
        elif token_type == "block_quote":
            return self._render_block_quote(token)
        elif token_type == "table":
            return self._render_table(token)
        elif token_type == "thematic_break":
            return self._render_thematic_break(token)
        elif token_type == "image":
            return self._render_image(token)
        else:
            # 未知的块级元素：记录警告，跳过
            self.warnings.append(f"未支持的块类型: {token_type}")
            return None

    def _render_heading(self, token: dict) -> FeishuBlock:
        """渲染标题块"""
        attrs = token.get("attrs", {})
        level = attrs.get("level", 1)
        # level 1 → HEADING1 (3), level 2 → HEADING2 (4), ...
        block_type = BlockType(2 + level)
        runs = self._render_children(token.get("children", []))
        return FeishuBlock(block_type, text_runs=runs)

    def _render_paragraph(self, token: dict) -> FeishuBlock:
        """渲染段落块"""
        runs = self._render_children(token.get("children", []))
        return FeishuBlock(BlockType.TEXT, text_runs=runs)

    def _render_list(self, token: dict) -> list[FeishuBlock]:
        """渲染列表（有序或无序）"""
        attrs = token.get("attrs", {})
        ordered = attrs.get("ordered", False)
        block_type = BlockType.ORDERED if ordered else BlockType.BULLET

        blocks: list[FeishuBlock] = []
        children = token.get("children", [])

        for item in children:
            if item.get("type") == "list_item":
                runs = self._render_children(item.get("children", []))
                blocks.append(FeishuBlock(block_type, text_runs=runs, indent_level=self.indent_level))
            elif item.get("type") == "list":
                # 嵌套列表：增加层级
                self.indent_level += 1
                if self.indent_level > 8:
                    self.warnings.append("列表嵌套超过 8 层，已截断")
                    self.indent_level = 8
                nested = self._render_list(item)
                self.indent_level -= 1
                blocks.extend(nested)

        return blocks

    def _render_block_code(self, token: dict) -> FeishuBlock:
        """渲染代码块"""
        attrs = token.get("attrs", {})
        language = attrs.get("info", "").strip() if attrs else None
        raw = token.get("raw", "")
        return FeishuBlock(BlockType.CODE, text_runs=[TextRun(raw or "​")], code_language=language)

    def _render_block_quote(self, token: dict) -> FeishuBlock:
        """渲染引用块"""
        runs: list[TextRun] = []
        children = token.get("children", [])
        for child in children:
            if child.get("type") == "paragraph":
                runs.extend(self._render_children(child.get("children", [])))
            else:
                runs.extend(self._render_children([child]))
        return FeishuBlock(BlockType.QUOTE, text_runs=runs)

    def _render_table(self, token: dict) -> FeishuBlock:
        """渲染表格"""
        cells: list[list[TextRun]] = []

        for part in token.get("children", []):
            # part 可能是 table_head、table_body 或直接的 table_row
            part_type = part.get("type", "")
            if part_type in ("table_head", "table_body"):
                rows = part.get("children", [])
            elif part_type == "table_row":
                rows = [part]
            else:
                continue
            for row in rows:
                if row.get("type") == "table_row":
                    row_cells = self._render_table_row(row)
                    if row_cells:
                        cells.append(row_cells)

        return FeishuBlock(BlockType.TABLE, text_runs=[], table_data=cells)

    def _render_table_row(self, row: dict) -> list[TextRun]:
        """渲染单个表格行的所有单元格内容"""
        row_cells: list[TextRun] = []
        for cell in row.get("children", []):
            if cell.get("type") == "table_cell":
                row_cells.extend(self._render_children(cell.get("children", [])))
        return row_cells

    def _render_thematic_break(self, token: dict) -> FeishuBlock:
        """渲染分隔线"""
        return FeishuBlock(BlockType.TEXT, text_runs=[TextRun("─" * 8)])

    def _render_image(self, token: dict) -> FeishuBlock | None:
        """渲染图片"""
        attrs = token.get("attrs", {})
        url = attrs.get("url", "")
        alt = attrs.get("alt", "")

        if not self.handle_images:
            self.warnings.append(f"跳过图片: {alt} ({url})")
            return None

        return FeishuBlock(BlockType.IMAGE, text_runs=[TextRun(alt)], image_url=url)

    def _render_children(self, children: list[dict]) -> list[TextRun]:
        """渲染子元素（内联元素）"""
        runs: list[TextRun] = []
        for child in children:
            run = self._render_inline(child)
            if isinstance(run, TextRun):
                runs.append(run)
            elif isinstance(run, list):
                runs.extend(run)
        return _merge_runs_with_style(runs)

    def _render_inline(self, token: dict) -> TextRun | list[TextRun] | None:
        """渲染内联元素"""
        token_type = token.get("type", "")

        if token_type == "text":
            raw = token.get("raw", "")
            return TextRun(raw)

        elif token_type in ("strong", "emphasis", "strikethrough"):
            runs = self._render_children(token.get("children", []))
            text = _runs_to_text(runs)
            style = TextStyle(
                bold=token_type == "strong",
                italic=token_type == "emphasis",
                strikethrough=token_type == "strikethrough",
            )
            return TextRun(text, style)

        elif token_type == "link":
            attrs = token.get("attrs", {})
            url = attrs.get("url", "")
            runs = self._render_children(token.get("children", []))
            text = _runs_to_text(runs)
            return TextRun(text, TextStyle(link=url))

        elif token_type == "codespan":
            raw = token.get("raw", "")
            return TextRun(raw, TextStyle(inline_code=True))

        elif token_type == "image":
            # 内联图片
            return None  # 图片作为块处理，内联中跳过

        elif token_type in ("softbreak", "hardbreak"):
            return TextRun("\n")

        else:
            # 未知内联元素：转为文本
            raw = token.get("raw", "")
            if raw:
                return TextRun(raw)
            return None


# === 核心转换函数 ===


def markdown_to_feishu_blocks(
    md: str,
    *,
    max_blocks: int = 50,
    handle_images: bool = False,
) -> MarkdownConversionResult:
    """将 Markdown 文本转换为飞书文档块列表。

    Args:
        md: Markdown 文本
        max_blocks: 最大块数限制（飞书单次 API 限制，默认 50）
        handle_images: 是否处理图片（需要上传逻辑）

    Returns:
        MarkdownConversionResult: 包含块列表、警告和统计信息

    Example:
        >>> result = markdown_to_feishu_blocks("# Title\\n\\nParagraph")
        >>> print(result.blocks[0].block_type)  # BlockType.HEADING1
        >>> print(result.stats)  # {'total_blocks': 2, 'headings': 1, ...}
    """
    if not md or not md.strip():
        return MarkdownConversionResult(blocks=[], warnings=["空内容"])

    # 检查 mistune 是否可用
    if not _HAS_MISTUNE:
        _logger.warning("mistune 未安装，回退到纯文本模式")
        return MarkdownConversionResult(
            blocks=[FeishuBlock(BlockType.TEXT, text_runs=[TextRun(md)])],
            warnings=["mistune 未安装，无法进行富文本渲染。请安装: pip install 'miniagent-python[feishu]'"],
            stats={"total_blocks": 1},
        )

    # 使用 mistune 解析为 AST，启用 GFM 插件
    md_parser = mistune.create_markdown(
        renderer=None,
        escape=False,
        plugins=['strikethrough', 'table'],  # GFM 扩展
    )

    try:
        tokens = md_parser(md)
        if not isinstance(tokens, list):
            # 如果返回的不是列表，可能是解析错误
            tokens = []
    except Exception as e:
        _logger.warning(f"Markdown 解析失败，回退到纯文本: {e}")
        # 回退：转为单个文本块
        return MarkdownConversionResult(
            blocks=[FeishuBlock(BlockType.TEXT, text_runs=[TextRun(md)])],
            warnings=[f"Markdown 解析失败: {e}"],
            stats={"total_blocks": 1},
        )

    # 使用渲染器转换 AST
    renderer = FeishuASTRenderer(handle_images=handle_images)
    blocks = renderer.render(tokens)

    # 截断检查
    warnings = renderer.warnings
    if len(blocks) > max_blocks:
        blocks = blocks[:max_blocks]
        warnings.append(f"内容过长，已截断为 {max_blocks} 个块")

    # 统计信息
    stats = {
        "total_blocks": len(blocks),
        "headings": sum(1 for b in blocks if b.block_type in range(3, 9)),
        "lists": sum(1 for b in blocks if b.block_type in (BlockType.BULLET, BlockType.ORDERED)),
        "code_blocks": sum(1 for b in blocks if b.block_type == BlockType.CODE),
        "quotes": sum(1 for b in blocks if b.block_type == BlockType.QUOTE),
        "tables": sum(1 for b in blocks if b.block_type == BlockType.TABLE),
        "images": sum(1 for b in blocks if b.block_type == BlockType.IMAGE),
    }

    return MarkdownConversionResult(blocks=blocks, warnings=warnings, stats=stats)


def build_lark_blocks_from_intermediate(blocks: list[FeishuBlock]) -> list[Any]:
    """将 FeishuBlock 中间表示转换为 lark-oapi SDK 的 Block 对象。

    Args:
        blocks: FeishuBlock 列表（来自 markdown_to_feishu_blocks）

    Returns:
        lark-oapi Block 对象列表，可直接用于 CreateDocumentBlockChildrenRequest

    Note:
        表格块 (TABLE) 需要特殊处理，不能直接转为 Block 对象。
        图片块 (IMAGE) 如果没有 token 也需要特殊处理。

        飞书 API 对不同块类型有特殊要求：
        - 代码块：需要 style.language 属性
        - 列表块：需要 style.indentation_level 属性
    """
    from lark_oapi.api.docx.v1 import (
        BlockBuilder,
        Text,
    )

    result: list[Any] = []

    for block in blocks:
        # 表格块需要特殊处理（不在此函数中转换）
        if block.block_type == BlockType.TABLE:
            continue  # 表格由 _handle_markdown_table 处理

        # 图片块如果没有 token，跳过
        if block.block_type == BlockType.IMAGE and not block.image_token:
            continue  # 需要先上传图片

        # 构建 TextElement 列表
        elements = _build_text_elements(block.text_runs)

        # 根据 block_type 构建不同的 Block
        try:
            # 基础 Text 对象（包含 elements）
            text_obj = Text.builder().elements(elements).build()

            if block.block_type == BlockType.TEXT:
                lark_block = (
                    BlockBuilder()
                    .block_type(2)
                    .text(text_obj)
                    .build()
                )

            elif block.block_type == BlockType.HEADING1:
                lark_block = (
                    BlockBuilder()
                    .block_type(3)
                    .heading1(text_obj)
                    .build()
                )

            elif block.block_type == BlockType.HEADING2:
                lark_block = (
                    BlockBuilder()
                    .block_type(4)
                    .heading2(text_obj)
                    .build()
                )

            elif block.block_type == BlockType.HEADING3:
                lark_block = (
                    BlockBuilder()
                    .block_type(5)
                    .heading3(text_obj)
                    .build()
                )

            elif block.block_type == BlockType.HEADING4:
                lark_block = (
                    BlockBuilder()
                    .block_type(6)
                    .heading4(text_obj)
                    .build()
                )

            elif block.block_type == BlockType.HEADING5:
                lark_block = (
                    BlockBuilder()
                    .block_type(7)
                    .heading5(text_obj)
                    .build()
                )

            elif block.block_type == BlockType.HEADING6:
                lark_block = (
                    BlockBuilder()
                    .block_type(8)
                    .heading6(text_obj)
                    .build()
                )

            elif block.block_type in (BlockType.BULLET, BlockType.ORDERED, BlockType.CODE):
                # 列表项/代码块统一降级为带前缀的纯文本块（去除内联样式）
                if block.block_type == BlockType.BULLET:
                    prefix = "- "
                elif block.block_type == BlockType.ORDERED:
                    prefix = "1. "
                else:
                    prefix = ""
                plain_elements = _build_text_elements(_plain_runs(block.text_runs, prefix=prefix))
                plain_text = Text.builder().elements(plain_elements).build()
                lark_block = (
                    BlockBuilder()
                    .block_type(2)
                    .text(plain_text)
                    .build()
                )

            elif block.block_type == BlockType.QUOTE:
                lark_block = (
                    BlockBuilder()
                    .block_type(12)
                    .quote(text_obj)
                    .build()
                )

            elif block.block_type == BlockType.IMAGE:
                # 图片块需要 image_token
                if block.image_token:
                    lark_block_dict = {
                        "block_type": 14,
                        "image": {"token": block.image_token},
                    }
                    result.append(lark_block_dict)
                continue

            else:
                # 默认：文本块
                lark_block = (
                    BlockBuilder()
                    .block_type(2)
                    .text(text_obj)
                    .build()
                )

            result.append(lark_block)

        except Exception as e:
            _logger.warning(f"构建 Block 失败 ({block.block_type}): {e}")
            # 回退：转为文本块
            try:
                fallback = (
                    BlockBuilder()
                    .block_type(2)
                    .text(Text.builder().elements(elements).build())
                    .build()
                )
                result.append(fallback)
            except Exception:
                _logger.error("回退 Block 也失败，跳过此块")
                continue

    return result


def _build_text_elements(runs: list[TextRun]) -> list[Any]:
    """将 TextRun 列表转换为 lark-oapi TextElement 列表。

    处理：
    - 文本分片（单次不超过 1800 字符）
    - 样式应用（bold, italic, link 等）
    """
    from lark_oapi.api.docx.v1 import (
        TextElement,
        TextElementStyle,
        TextRun,
    )

    elements: list[Any] = []

    for run in runs:
        # 分片长文本
        chunks = _chunk_text(run.content)

        for chunk in chunks:
            try:
                text_run_builder = TextRun.builder().content(chunk)

                # 应用样式
                if run.style.bold or run.style.italic:
                    style_builder = TextElementStyle.builder()
                    if run.style.bold:
                        style_builder.bold(True)
                    if run.style.italic:
                        style_builder.italic(True)
                    text_run_builder.text_element_style(style_builder.build())

                text_run = text_run_builder.build()
                element = TextElement.builder().text_run(text_run).build()
                elements.append(element)

            except Exception as e:
                _logger.warning(f"构建 TextElement 失败: {e}")
                # 回退：无样式的纯文本
                try:
                    plain_run = TextRun.builder().content(chunk).build()
                    elements.append(TextElement.builder().text_run(plain_run).build())
                except Exception:
                    _logger.error("回退 TextElement 也失败，跳过")
                    continue

    return elements


def estimate_block_count(md: str) -> int:
    """预估 Markdown 文本将生成的块数量。

    用于提前检查是否超出 API 限制。
    """
    if not md:
        return 0
    # 简单估算：行数（不含空行）
    lines = [line for line in md.splitlines() if line.strip()]
    return len(lines)


__all__ = [
    "BlockType",
    "FeishuBlock",
    "TextRun",
    "TextStyle",
    "MarkdownConversionResult",
    "markdown_to_feishu_blocks",
    "build_lark_blocks_from_intermediate",
    "estimate_block_count",
]
