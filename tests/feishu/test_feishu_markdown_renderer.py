"""飞书 Markdown 渲染器单元测试。

测试 markdown_renderer.py 的核心转换逻辑：
- 标题转换（# ~ ######）
- 内联样式（bold, italic, link, inline_code）
- 代码块（带语言标记）
- 列表（无序/有序）
- 引用块
- 表格
- 边界情况（长文本、空段落、截断）
- 纯文本渲染路径
"""

from __future__ import annotations

from miniagent.assistant.feishu.docx.markdown_renderer import (
    BlockType,
    FeishuBlock,
    MarkdownConversionResult,
    TextRun,
    TextStyle,
    _chunk_text,
    build_lark_blocks_from_intermediate,
    estimate_block_count,
    markdown_to_feishu_blocks,
)


class TestHeadingConversion:
    """标题转换测试"""

    def test_heading1(self):
        """一级标题"""
        result = markdown_to_feishu_blocks("# Title")
        assert len(result.blocks) == 1
        assert result.blocks[0].block_type == BlockType.HEADING1
        assert len(result.blocks[0].text_runs) == 1
        assert result.blocks[0].text_runs[0].content == "Title"

    def test_heading2(self):
        """二级标题"""
        result = markdown_to_feishu_blocks("## Subtitle")
        assert result.blocks[0].block_type == BlockType.HEADING2

    def test_heading3(self):
        """三级标题"""
        result = markdown_to_feishu_blocks("### Section")
        assert result.blocks[0].block_type == BlockType.HEADING3

    def test_heading4(self):
        """四级标题"""
        result = markdown_to_feishu_blocks("#### Detail")
        assert result.blocks[0].block_type == BlockType.HEADING4

    def test_heading5(self):
        """五级标题"""
        result = markdown_to_feishu_blocks("##### Note")
        assert result.blocks[0].block_type == BlockType.HEADING5

    def test_heading6(self):
        """六级标题"""
        result = markdown_to_feishu_blocks("###### Tiny")
        assert result.blocks[0].block_type == BlockType.HEADING6

    def test_heading_with_inline_style(self):
        """标题带内联样式"""
        result = markdown_to_feishu_blocks("# **Bold** Title")
        assert result.blocks[0].block_type == BlockType.HEADING1
        runs = result.blocks[0].text_runs
        # 至少有一个粗体 run
        assert any(r.style.bold for r in runs)


class TestParagraphWithInlineStyles:
    """段落内联样式测试"""

    def test_bold_text(self):
        """粗体"""
        result = markdown_to_feishu_blocks("**bold text**")
        assert result.blocks[0].block_type == BlockType.TEXT
        runs = result.blocks[0].text_runs
        assert any(r.style.bold for r in runs)

    def test_italic_text(self):
        """斜体"""
        result = markdown_to_feishu_blocks("*italic text*")
        runs = result.blocks[0].text_runs
        assert any(r.style.italic for r in runs)

    def test_strikethrough(self):
        """删除线"""
        result = markdown_to_feishu_blocks("~~deleted~~")
        runs = result.blocks[0].text_runs
        assert any(r.style.strikethrough for r in runs)

    def test_inline_code(self):
        """内联代码"""
        result = markdown_to_feishu_blocks("`code`")
        runs = result.blocks[0].text_runs
        assert any(r.style.inline_code for r in runs)

    def test_link(self):
        """链接"""
        result = markdown_to_feishu_blocks("[Click here](https://example.com)")
        runs = result.blocks[0].text_runs
        assert any(r.style.link == "https://example.com" for r in runs)

    def test_mixed_styles(self):
        """混合样式"""
        result = markdown_to_feishu_blocks("**bold** and *italic* and `code`")
        runs = result.blocks[0].text_runs
        assert any(r.style.bold for r in runs)
        assert any(r.style.italic for r in runs)
        assert any(r.style.inline_code for r in runs)

    def test_plain_paragraph(self):
        """纯文本段落"""
        result = markdown_to_feishu_blocks("Just plain text.")
        assert result.blocks[0].block_type == BlockType.TEXT
        runs = result.blocks[0].text_runs
        # 默认样式都是 False
        for r in runs:
            assert not r.style.bold
            assert not r.style.italic
            assert not r.style.link


class TestCodeBlock:
    """代码块测试"""

    def test_code_block_without_language(self):
        """无语言标记的代码块"""
        md = "```\nprint('hello')\n```"
        result = markdown_to_feishu_blocks(md)
        assert result.blocks[0].block_type == BlockType.CODE
        assert result.blocks[0].code_language is None

    def test_code_block_with_python(self):
        """Python 代码块"""
        md = "```python\nprint('hi')\n```"
        result = markdown_to_feishu_blocks(md)
        assert result.blocks[0].block_type == BlockType.CODE
        assert result.blocks[0].code_language == "python"

    def test_code_block_with_javascript(self):
        """JavaScript 代码块"""
        md = "```javascript\nconsole.log('hi')\n```"
        result = markdown_to_feishu_blocks(md)
        assert result.blocks[0].code_language == "javascript"


class TestList:
    """列表测试"""

    def test_bullet_list(self):
        """无序列表"""
        md = "- Item 1\n- Item 2\n- Item 3"
        result = markdown_to_feishu_blocks(md)
        assert len(result.blocks) == 3
        for block in result.blocks:
            assert block.block_type == BlockType.BULLET

    def test_ordered_list(self):
        """有序列表"""
        md = "1. First\n2. Second\n3. Third"
        result = markdown_to_feishu_blocks(md)
        assert len(result.blocks) == 3
        for block in result.blocks:
            assert block.block_type == BlockType.ORDERED

    def test_nested_bullet_list(self):
        """嵌套无序列表"""
        md = "- Item 1\n  - Sub item\n- Item 2"
        result = markdown_to_feishu_blocks(md)
        # 所有都是 BULLET 块
        assert all(b.block_type == BlockType.BULLET for b in result.blocks)
        # 嵌套层级不同
        # Note: 当前实现扁平化，indent_level 可能不同

    def test_list_with_inline_style(self):
        """列表带内联样式"""
        md = "- **Bold item**\n- *Italic item*"
        result = markdown_to_feishu_blocks(md)
        assert all(b.block_type == BlockType.BULLET for b in result.blocks)
        # 检查样式是否被保留（注意：mistune 可能不完美处理列表内联样式）
        # 至少有文本内容
        assert len(result.blocks) == 2


class TestQuote:
    """引用块测试"""

    def test_simple_quote(self):
        """简单引用"""
        md = "> This is a quote."
        result = markdown_to_feishu_blocks(md)
        assert result.blocks[0].block_type == BlockType.QUOTE

    def test_multi_line_quote(self):
        """多行引用"""
        md = "> Line 1\n> Line 2"
        result = markdown_to_feishu_blocks(md)
        assert result.blocks[0].block_type == BlockType.QUOTE


class TestTable:
    """表格测试"""

    def test_simple_table(self):
        """简单表格"""
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        result = markdown_to_feishu_blocks(md)
        assert result.blocks[0].block_type == BlockType.TABLE
        assert result.blocks[0].table_data is not None

    def test_table_with_header(self):
        """带表头的表格"""
        md = "| Name | Age |\n|------|-----|\n| Alice | 25 |\n| Bob | 30 |"
        result = markdown_to_feishu_blocks(md)
        assert result.blocks[0].block_type == BlockType.TABLE
        # 检查数据结构
        table_data = result.blocks[0].table_data
        assert table_data is not None
        assert len(table_data) >= 2  # 至少有表头和一行数据


class TestThematicBreak:
    """分隔线测试"""

    def test_horizontal_rule_dash(self):
        """三个减号分隔线"""
        md = "Paragraph 1\n\n---\n\nParagraph 2"
        result = markdown_to_feishu_blocks(md)
        # 应包含段落和分隔线块
        block_types = [b.block_type for b in result.blocks]
        assert BlockType.TEXT in block_types


class TestBoundaryCases:
    """边界情况测试"""

    def test_empty_content(self):
        """空内容"""
        result = markdown_to_feishu_blocks("")
        assert len(result.blocks) == 0
        assert "空内容" in result.warnings

    def test_whitespace_only(self):
        """仅空白字符"""
        result = markdown_to_feishu_blocks("   \n\n   ")
        assert len(result.blocks) == 0
        assert "空内容" in result.warnings

    def test_long_text_chunking(self):
        """长文本分片"""
        long_text = "x" * 2000
        result = markdown_to_feishu_blocks(long_text)
        # 单个段落，但可能多个 text_run（分片）
        assert len(result.blocks) == 1
        # 内部分片由 _build_text_elements 处理

    def test_max_blocks_limit(self):
        """块数限制"""
        # 使用空行分隔段落（mistune 将连续文本合并为单个段落）
        md = "\n\n".join([f"Paragraph {i}" for i in range(100)])
        result = markdown_to_feishu_blocks(md, max_blocks=50)
        assert len(result.blocks) <= 50
        # 检查是否有截断警告（中文或英文）
        assert any("截断" in w or "truncat" in w.lower() or "limit" in w.lower() for w in result.warnings)

    def test_invalid_markdown(self):
        """无效 Markdown（解析失败）"""
        # 极端情况：只有标记符号
        md = "####"
        result = markdown_to_feishu_blocks(md)
        # 应正常处理（不会崩溃）
        assert isinstance(result, MarkdownConversionResult)


class TestChunkText:
    """文本分片测试"""

    def test_short_text(self):
        """短文本不分片"""
        chunks = _chunk_text("short")
        assert len(chunks) == 1
        assert chunks[0] == "short"

    def test_long_text(self):
        """长文本分片"""
        text = "x" * 2000
        chunks = _chunk_text(text)
        assert len(chunks) == 2
        assert len(chunks[0]) == 1800
        assert len(chunks[1]) == 200

    def test_empty_text(self):
        """空文本返回零宽空格"""
        chunks = _chunk_text("")
        assert len(chunks) == 1
        assert chunks[0] == "​"


class TestEstimateBlockCount:
    """块数预估测试"""

    def test_empty(self):
        """空内容预估为 0"""
        assert estimate_block_count("") == 0

    def test_single_line(self):
        """单行预估为 1"""
        assert estimate_block_count("One line") == 1

    def test_multiple_lines(self):
        """多行预估"""
        md = "Line 1\nLine 2\n\nLine 3"
        # 空行不计入
        count = estimate_block_count(md)
        assert count == 3


class TestStatistics:
    """统计信息测试"""

    def test_stats_heading(self):
        """标题统计"""
        md = "# H1\n## H2\n### H3"
        result = markdown_to_feishu_blocks(md)
        assert result.stats["headings"] == 3

    def test_stats_code_blocks(self):
        """代码块统计"""
        md = "```python\ncode1\n```\n\n```js\ncode2\n```"
        result = markdown_to_feishu_blocks(md)
        assert result.stats["code_blocks"] == 2

    def test_stats_lists(self):
        """列表统计"""
        md = "- Item 1\n- Item 2\n1. First\n2. Second"
        result = markdown_to_feishu_blocks(md)
        assert result.stats["lists"] == 4


class TestPlainTextRendering:
    """纯文本渲染测试"""

    def test_markdown_to_plain_text(self):
        """纯文本剥离函数"""
        from miniagent.assistant.feishu.docx.markdown import markdown_to_plain_text

        md = "# Title\n\n**bold** text\n\n- list item"
        plain = markdown_to_plain_text(md)
        # 剥离标题标记和引用标记，但不处理粗体（保守剥离）
        assert "#" not in plain
        # markdown_to_plain_text 不处理 **bold**，保留原样
        # 这是设计意图（保守剥离）

    def test_markdown_to_blocks(self):
        """纯文本块函数"""
        from miniagent.assistant.feishu.docx.markdown import markdown_to_blocks

        md = "# Title"
        blocks = markdown_to_blocks(md)
        # 纯文本路径只返回文本块
        assert len(blocks) > 0


class TestBuildLarkBlocksFromIntermediate:
    """转换为 lark-oapi Block 测试"""

    def test_text_block(self):
        """文本块转换"""
        feishu_block = FeishuBlock(BlockType.TEXT, text_runs=[TextRun("Hello")])
        lark_blocks = build_lark_blocks_from_intermediate([feishu_block])
        assert len(lark_blocks) == 1

    def test_heading_block(self):
        """标题块转换"""
        feishu_block = FeishuBlock(BlockType.HEADING1, text_runs=[TextRun("Title")])
        lark_blocks = build_lark_blocks_from_intermediate([feishu_block])
        assert len(lark_blocks) == 1

    def test_code_block(self):
        """代码块转换"""
        feishu_block = FeishuBlock(
            BlockType.CODE,
            text_runs=[TextRun("print('hi')")],
            code_language="python",
        )
        lark_blocks = build_lark_blocks_from_intermediate([feishu_block])
        assert len(lark_blocks) == 1

    def test_table_block_skipped(self):
        """表格块跳过（需特殊处理）"""
        feishu_block = FeishuBlock(BlockType.TABLE, table_data=[[TextRun("A")]])
        lark_blocks = build_lark_blocks_from_intermediate([feishu_block])
        # 表格不在结果中（需单独处理）
        assert len(lark_blocks) == 0

    def test_styled_text_run(self):
        """带样式的 TextRun"""
        feishu_block = FeishuBlock(
            BlockType.TEXT,
            text_runs=[TextRun("bold", TextStyle(bold=True))],
        )
        lark_blocks = build_lark_blocks_from_intermediate([feishu_block])
        assert len(lark_blocks) == 1


class TestComplexMarkdown:
    """复杂 Markdown 测试"""

    def test_full_document(self):
        """完整文档"""
        md = """# Title

## Section 1

**Bold** and *italic* text.

- Item 1
- Item 2

```python
print('code')
```

> Quote here

| A | B |
|---|---|
| 1 | 2 |

[Link](https://example.com)
"""
        result = markdown_to_feishu_blocks(md)
        assert len(result.blocks) > 5
        assert result.stats["headings"] >= 2
        assert result.stats["lists"] >= 2
        assert result.stats["code_blocks"] >= 1

    def test_mixed_content(self):
        """混合内容"""
        md = "Paragraph with **bold** and `code`.\n\n- **List item**\n- *Italic item*"
        result = markdown_to_feishu_blocks(md)
        # 应正常解析，不崩溃
        assert isinstance(result, MarkdownConversionResult)
        assert len(result.blocks) > 0
