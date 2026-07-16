"""Shared Markdown formatting owned by the command presentation layer."""

from __future__ import annotations


def escape_markdown_cell(text: str) -> str:
    """Normalize one GFM table cell without changing other Markdown text."""
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    return normalized.replace("|", "\\|").replace("\n", " ").strip()


__all__ = ["escape_markdown_cell"]
