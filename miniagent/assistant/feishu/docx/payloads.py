"""Pure Docx text payload builders shared by Markdown and block operations."""

from __future__ import annotations

from typing import Any

from miniagent.agent.constants import DOCX_APPEND_MAX_BLOCKS

_TEXT_RUN_MAX = 1800
_BLOCK_TEXT = 2


def chunk_text_runs(line: str) -> list[str]:
    if not line:
        return ["\u200b"]
    return [line[index : index + _TEXT_RUN_MAX] for index in range(0, len(line), _TEXT_RUN_MAX)]


def paragraph_blocks_for_text(text: str) -> list[Any]:
    from lark_oapi.api.docx.v1 import BlockBuilder, Text, TextElement, TextRun

    blocks = []
    for raw in (text.split("\n") or [""])[:DOCX_APPEND_MAX_BLOCKS]:
        elements = [
            TextElement.builder().text_run(TextRun.builder().content(run).build()).build()
            for run in chunk_text_runs(raw)
        ]
        blocks.append(
            BlockBuilder()
            .block_type(_BLOCK_TEXT)
            .text(Text.builder().elements(elements).build())
            .build()
        )
    return blocks


__all__ = ["chunk_text_runs", "paragraph_blocks_for_text"]
