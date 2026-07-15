"""轻量 Markdown → docx 纯文本块（不含 MD 表格）。"""

from __future__ import annotations

from miniagent.assistant.feishu.docx.blocks import _paragraph_blocks_for_text

_HEADING = ("#", "##", "###", "####", "#####", "######")


def markdown_to_plain_text(md: str) -> str:
    """保守剥离常见 MD 标记，输出适合 append 的纯文本。"""
    lines_out: list[str] = []
    in_fence = False
    for raw in (md or "").splitlines():
        line = raw.rstrip()
        if line.strip().startswith("```"):
            in_fence = not in_fence
            if in_fence:
                lines_out.append("```")
            continue
        if in_fence:
            lines_out.append(line)
            continue
        stripped = line.lstrip()
        for h in _HEADING:
            if stripped.startswith(h + " "):
                stripped = stripped[len(h) + 1 :]
                break
        if stripped.startswith("> "):
            stripped = stripped[2:]
        lines_out.append(stripped)
    return "\n".join(lines_out)


def markdown_to_blocks(md: str) -> list:
    """转为 docx 文本块列表（段落级）。"""
    return _paragraph_blocks_for_text(markdown_to_plain_text(md))


__all__ = ["markdown_to_blocks", "markdown_to_plain_text"]
