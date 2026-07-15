"""飞书卡片 Markdown 规范化、分片和流式 PATCH 策略。"""

from __future__ import annotations

import re

from miniagent.agent.constants import (
    FEISHU_PATCH_BUDGET,
    FEISHU_PATCH_CHAR_DELTA,
    FEISHU_PATCH_IMPORTANT_CONTENT_IMMEDIATE,
    FEISHU_PATCH_INTERVAL_S,
)
from miniagent.assistant.infrastructure.json_config import get_config

_RE_LONE_ASTERISK = re.compile(r"(?<!\*)\*(?!\*)")
_RE_TRIPLE_NEWLINE = re.compile(r"\n{3,}")
_RE_BR_TAG = re.compile(r"<br\s*/?>", re.IGNORECASE)
_RE_FENCE_LINE = re.compile(r"^(`{3,})(.*)$")
_RE_ATX_HEADING = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_RE_HORIZONTAL_RULE = re.compile(r"(?m)^[ \t]*(?:---+|\*{3,}|_{3,})[ \t]*$")
_RE_CODE_FENCE = re.compile(r"```[^\n]*\n([\s\S]*?)```")
_RE_INLINE_CODE = re.compile(r"`([^`]+)`")
_RE_BOLD_STAR = re.compile(r"\*\*([^*]+)\*\*")
_RE_BOLD_UNDERSCORE = re.compile(r"__([^_]+)__")

FEISHU_THINKING_PATCH_MIN_INTERVAL_S = float(FEISHU_PATCH_INTERVAL_S)
FEISHU_THINKING_PATCH_MIN_CHAR_DELTA = int(FEISHU_PATCH_CHAR_DELTA)
FEISHU_THINKING_PATCH_BUDGET = int(FEISHU_PATCH_BUDGET)


def normalize_im_receive_chat_id(chat_id: str) -> str:
    """去掉内部路由前缀，得到飞书 IM API 可用的 receive id。"""
    normalized = (chat_id or "").strip()
    return normalized.removeprefix("feishu:")


def is_valid_im_receive_id(chat_id: str) -> bool:
    """判断群聊或用户标识是否可作为 IM API 的 receive id。"""
    normalized = (chat_id or "").strip()
    return bool(normalized) and normalized.startswith(("oc_", "ou_"))


def is_important_content_for_immediate_patch(text: str) -> bool:
    """识别代码、标题、表格或列表等应立即刷新的结构化内容。"""
    if not text or not FEISHU_PATCH_IMPORTANT_CONTENT_IMMEDIATE:
        return False
    stripped = text.strip()
    fence_count = text.count("```")
    if fence_count > 0 and fence_count % 2 == 1:
        return True
    if stripped.startswith("#") and len(stripped) > 1 and stripped[1] in (" ", "#"):
        return True
    if stripped.startswith("|") and "|" in stripped:
        return True
    return stripped.startswith(("- ", "* ", "1. "))


def adjust_patch_budget_dynamically(text_len: int, current_budget: int) -> int:
    """随累计正文长度增加 PATCH 预算，但不缩减调用方已有预算。"""
    if text_len > 10_000 and current_budget < FEISHU_THINKING_PATCH_BUDGET + 40:
        return FEISHU_THINKING_PATCH_BUDGET + 40
    if text_len > 5_000 and current_budget < FEISHU_THINKING_PATCH_BUDGET + 20:
        return FEISHU_THINKING_PATCH_BUDGET + 20
    return current_budget


def feishu_card_body_max() -> int:
    """返回单张交互卡片 Markdown 正文的安全字符上限。"""
    value = get_config("feishu.card.body_max_chars", 48_000)
    return max(1_000, int(value)) if value else 48_000


def feishu_card_thinking_max() -> int:
    """返回思考流卡片上限，未配置时继承普通卡片上限。"""
    value = get_config("feishu.card.thinking_max_chars", None)
    return max(1_000, int(value)) if value is not None else feishu_card_body_max()


def normalize_lark_md(text: str) -> str:
    """把常见 GFM/HTML 安全降级为飞书 ``lark_md`` 支持的子集。"""
    if not text:
        return ""
    normalized = text.replace("\ufeff", "").replace("\u200b", "").replace("\u200c", "")
    normalized = _neutralize_lone_asterisks(normalized.replace("\ufffd", ""))
    normalized = _RE_BR_TAG.sub("\n", normalized)
    normalized = "\n".join(_collapse_fence_line(line) for line in normalized.split("\n"))
    normalized = _RE_ATX_HEADING.sub(r"**\2**", normalized)

    from miniagent.assistant.feishu.cards.gfm_table import (
        find_gfm_table_block,
        gfm_table_block_to_bullet_list,
    )

    lines = normalized.split("\n")
    output: list[str] = []
    index = 0
    while index < len(lines):
        found = find_gfm_table_block(lines, index)
        if found is None:
            output.append(lines[index])
            index += 1
            continue
        begin, end = found
        bullet_list = gfm_table_block_to_bullet_list(lines[begin:end])
        if bullet_list:
            output.append(bullet_list)
        index = end
    joined = "\n".join(output)
    return _RE_HORIZONTAL_RULE.sub("────────", joined)


def prepare_thinking_body_for_card(
    raw: str,
    *,
    apply_cap: bool = True,
    max_len: int | None = None,
) -> str:
    """清理思考正文、应用长度帽并转换为 ``lark_md``。"""
    cap = feishu_card_body_max() if max_len is None else max_len
    text = (raw or "").replace("\r", "").replace("\t", "  ")
    text = _RE_TRIPLE_NEWLINE.sub("\n\n", text)
    if apply_cap and len(text) > cap:
        text = text[:cap] + "…"
    return normalize_lark_md(text)


def prepare_card_markdown(raw: str, max_len: int | None = None, *, normalize: bool = True) -> str:
    """为最终回复应用长度帽、制表符归一化和可选 GFM 降级。"""
    cap = feishu_card_body_max() if max_len is None else max_len
    text = raw if len(raw) <= cap else raw[:cap] + "…"
    text = text.replace("\r", "").replace("\t", "  ")
    return normalize_lark_md(text) if normalize else text


def prepare_thinking_markdown(raw: str) -> str:
    """使用思考流专用上限准备卡片正文。"""
    return prepare_thinking_body_for_card(raw, max_len=feishu_card_thinking_max())


def strip_light_markdown_for_plain(text: str) -> str:
    """弱化代码围栏、行内代码和粗体标记，供纯文本降级使用。"""
    result = (text or "").replace("\r\n", "\n")
    result = _RE_CODE_FENCE.sub(r"\1", result)
    result = _RE_INLINE_CODE.sub(r"\1", result)
    previous = None
    while previous != result:
        previous = result
        result = _RE_BOLD_STAR.sub(r"\1", result)
        result = _RE_BOLD_UNDERSCORE.sub(r"\1", result)
    return result


def chunk_card_markdown(
    reply: str,
    max_len: int | None = None,
    *,
    already_normalized: bool = False,
) -> list[str]:
    """把长正文切成多张卡片，并尽量避免切开代码围栏。"""
    cap = feishu_card_body_max() if max_len is None else max_len
    text = (reply or "").replace("\r", "").replace("\t", "  ")
    if not already_normalized:
        text = normalize_lark_md(text)
    if cap <= 0 or len(text) <= cap:
        return [text]
    chunks: list[str] = []
    rest = text
    while rest:
        if len(rest) <= cap:
            chunks.append(rest)
            break
        cut = _chunk_cut_index(rest, cap)
        chunks.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")
    return chunks


def _collapse_fence_line(line: str) -> str:
    match = _RE_FENCE_LINE.match(line)
    return "```" + match.group(2) if match and len(match.group(1)) > 3 else line


def _neutralize_lone_asterisks(text: str) -> str:
    lines = text.split("\n")
    in_fence = False
    result: list[str] = []
    for line in lines:
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        elif not in_fence:
            line = _RE_LONE_ASTERISK.sub("＊", line)
        result.append(line)
    return "\n".join(result)


def _chunk_tail_has_unclosed_fence(chunk: str) -> bool:
    return sum(1 for line in chunk.split("\n") if line.strip().startswith("```")) % 2 == 1


def _chunk_cut_index(text: str, cap: int) -> int:
    cut = text.rfind("\n", 0, cap)
    if cut < max(cap // 2, 1):
        cut = cap
    limit = min(len(text), cut + max(8_000, cap // 8))
    while cut < limit:
        if not _chunk_tail_has_unclosed_fence(text[:cut]):
            return cut
        next_line = text.find("\n", cut)
        if next_line == -1:
            return len(text)
        cut = next_line + 1
    return cut


__all__ = [
    "FEISHU_THINKING_PATCH_BUDGET",
    "FEISHU_THINKING_PATCH_MIN_CHAR_DELTA",
    "FEISHU_THINKING_PATCH_MIN_INTERVAL_S",
    "adjust_patch_budget_dynamically",
    "chunk_card_markdown",
    "feishu_card_body_max",
    "feishu_card_thinking_max",
    "is_important_content_for_immediate_patch",
    "is_valid_im_receive_id",
    "normalize_im_receive_chat_id",
    "normalize_lark_md",
    "prepare_card_markdown",
    "prepare_thinking_body_for_card",
    "prepare_thinking_markdown",
    "strip_light_markdown_for_plain",
]
