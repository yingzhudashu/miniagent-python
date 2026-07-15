"""从飞书 URL 或原始参数解析 document_id / bitable app_token / table_id。"""

from __future__ import annotations

import re

_DOCX = re.compile(r"/docx/([A-Za-z0-9_-]+)", re.I)
_BASE = re.compile(r"/base/([A-Za-z0-9_-]+)", re.I)
_TBL = re.compile(r"[?&]table(?:_id)?=([A-Za-z0-9_-]+)", re.I)


def extract_doc_token(raw: str | None) -> str:
    """解析云文档 token：URL 取 ``/docx/`` 段，否则返回去空白后的原字符串。"""
    s = (raw or "").strip()
    if not s:
        return ""
    if s.startswith("http") or "/docx/" in s.lower():
        m = _DOCX.search(s.replace("\\", "/"))
        return m.group(1) if m else ""
    return s


def extract_bitable_app_token(raw: str | None) -> str:
    """解析多维表格 app_token：URL 取 ``/base/`` 段，否则返回去空白后的原字符串。"""
    s = (raw or "").strip()
    if not s:
        return ""
    if s.startswith("http") or "/base/" in s.lower():
        m = _BASE.search(s.replace("\\", "/"))
        return m.group(1) if m else ""
    return s


def extract_table_id(raw: str | None, *, url_hint: str | None = None) -> str:
    """解析 table_id：优先 ``tbl`` 前缀 token，否则从 URL 查询参数 ``table`` / ``table_id`` 提取。"""
    for src in (raw, url_hint):
        s = (src or "").strip()
        if not s:
            continue
        if s.startswith("tbl") or (len(s) > 8 and "/" not in s and "?" not in s):
            return s
        m = _TBL.search(s.replace("\\", "/"))
        if m:
            return m.group(1)
    return ""


__all__ = ["extract_bitable_app_token", "extract_doc_token", "extract_table_id"]
