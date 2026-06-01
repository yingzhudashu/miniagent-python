"""渐进式会话历史压缩：按单工具 → 单步骤 → 整轮 thinking → 归档/删轮 顺序减量。

策略与配置见 ``docs/MEMORY_SYSTEM.md``、``docs/ARCHITECTURE.md``（上下文窗口）。
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from miniagent.infrastructure.json_config import get_config
from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)

# 与 UnifiedEngine._tool_finish 输出对齐的占位符（幂等检测用）
TOOL_OUTPUT_REDACTED_PLACEHOLDER = "（工具输出已压缩；完整内容见会话日记或 read_session_diary。）"
STEP_BODY_REDACTED_MARKER = "（本步骤推理与工具细节已压缩；结论见助手回复。）"
TURN_THINKING_REDACTED_MARKER = "（本轮思考与中间步骤已压缩。）"


class CompressionLevel(str, Enum):
    """压缩层级（与 debug 日志 ``action`` 字段一致）。"""

    TOOL_OUTPUT = "l1_tool_output"
    STEP_BODY = "l2_step_body"
    TURN_PROCESS = "l3_turn_process"
    TURN_REMOVE_ARCHIVE = "l4_archive"
    TURN_REMOVE_TRIM = "l4_trim"


def _progressive_enabled(explicit: bool | None) -> bool:
    """与 ``get_default_agent_config().history_progressive_compression`` 语义一致。"""
    if explicit is not None:
        return bool(explicit)
    return get_config("memory.history_progressive", True)


def _maintenance_max_iters() -> int:
    """单轮历史维护循环最大迭代次数。"""
    return max(1, get_config("memory.maintenance_max_iters", 500))


def _over_archive_limits(history: list[dict[str, Any]]) -> bool:
    """是否应触发 L4 归档路径（条数或 token 提示超阈值）。"""
    from miniagent.memory.history_archive import (
        history_archive_max_messages,
        history_archive_token_hint,
    )

    if len(history) > history_archive_max_messages():
        return True
    tok_hint = history_archive_token_hint()
    if tok_hint:
        from miniagent.memory.history_bridge import estimate_history_messages_tokens

        if estimate_history_messages_tokens(history) > tok_hint:
            return True
    return False


def _over_tail_cap(history: list[dict[str, Any]], cap: int) -> bool:
    """``cap`` 非负且当前消息数超过尾部上限时为真。"""
    return cap >= 0 and len(history) > cap


def _redact_tool_block_regex(text: str) -> tuple[str, bool]:
    """原正则路径：参数行内无反引号歧义时可靠。"""
    if not text:
        return text, False
    m = re.search(
        r"\*\*工具 `[^`]+`\*\*（(?:成功|失败)）\n"
        r"- 参数：`[\s\S]*?`\n"
        r"- 输出：\n"
        r"(`{3,47})\n",
        text,
    )
    if not m:
        return text, False
    opener = m.group(1)
    body_start = m.end()
    closer = "\n" + opener + "\n"
    end_body = text.find(closer, body_start)
    if end_body < 0:
        return text, False
    old_inner = text[body_start:end_body]
    full_end = end_body + len(closer)
    if old_inner.strip() == TOOL_OUTPUT_REDACTED_PLACEHOLDER.strip():
        tail, ok = redact_first_tool_output_in_text(text[full_end:])
        if ok:
            return text[:full_end] + tail, True
        return text, False
    new_block = text[m.start() : m.end()] + TOOL_OUTPUT_REDACTED_PLACEHOLDER + closer
    return text[: m.start()] + new_block + text[full_end:], True


def _redact_tool_block_delimited(text: str) -> tuple[str, bool]:
    """按 ``- 参数：`…`\n- 输出：`` 定界解析，参数内含反引号时以行末闭合为准。"""
    m = re.search(r"\*\*工具 `[^`]+`\*\*（(?:成功|失败)）\n", text)
    if not m:
        return text, False
    out_marker = "\n- 输出：\n"
    out_pos = text.find(out_marker, m.end())
    if out_pos < 0:
        return text, False
    chunk = text[m.end() : out_pos].lstrip("\n\r")
    param_line = None
    for ln in chunk.split("\n"):
        if ln.strip().startswith("- 参数："):
            param_line = ln
            break
    if not param_line:
        return text, False
    first = param_line.find("`")
    last = param_line.rfind("`")
    if first < 0 or last <= first:
        return text, False
    tail = text[out_pos + len(out_marker) :]
    fm = re.match(r"(`{3,47})\n", tail)
    if not fm:
        return text, False
    opener = fm.group(1)
    body_start = out_pos + len(out_marker) + len(fm.group(0))
    closer = "\n" + opener + "\n"
    end_body = text.find(closer, body_start)
    if end_body < 0:
        return text, False
    old_inner = text[body_start:end_body]
    full_end = end_body + len(closer)
    if old_inner.strip() == TOOL_OUTPUT_REDACTED_PLACEHOLDER.strip():
        tail2, ok = redact_first_tool_output_in_text(text[full_end:])
        if ok:
            return text[:full_end] + tail2, True
        return text, False
    prefix = text[m.start() : out_pos + len(out_marker)] + fm.group(0)
    new_block = prefix + TOOL_OUTPUT_REDACTED_PLACEHOLDER + closer
    return text[: m.start()] + new_block + text[full_end:], True


def redact_first_tool_output_in_text(text: str) -> tuple[str, bool]:
    """将正文内第一个仍含完整围栏输出的工具块替换为占位；优先定界解析，失败则回退正则。"""
    out, ok = _redact_tool_block_delimited(text)
    if ok:
        return out, True
    return _redact_tool_block_regex(text)


def _find_plan_line_for_step(text: str, step_no: int) -> str | None:
    """从 ``[评估与计划]`` / ``[执行计划]``（旧格式兼容）块中匹配 ``步骤概要`` 下的 ``{n}. …`` 行。"""
    # 新格式优先，兼容旧格式历史记录
    for marker in ("[评估与计划]", "[执行计划]"):
        idx = text.find(marker)
        if idx < 0:
            continue
        start = idx + len(marker)
        next_step = re.search(r"\n\[步骤\s*\d+", text[start:])
        end = start + next_step.start() if next_step else len(text)
        block = text[start:end]
        mm = re.compile(rf"^\s*{step_no}\.\s+(.+)$", re.MULTILINE).search(block)
        if mm:
            line = mm.group(1).strip()
            return line[:500] if len(line) > 500 else line
    return None


def compress_first_step_span_in_text(text: str) -> tuple[str, bool]:
    """将第一个仍含长正文的 ``[步骤 k/n]`` 段压成标题 + 可选计划行 + 说明（幂等）。"""
    step_hdr = re.compile(r"(\[步骤\s*\d+\s*/\s*\d+\s*\][^\n]*)")
    matches = list(step_hdr.finditer(text))
    if not matches:
        return text, False
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        span = text[start:end]
        if STEP_BODY_REDACTED_MARKER in span:
            continue
        header_line = m.group(1).strip()
        rest = span[len(m.group(0)) :].lstrip("\n")
        if len(rest.strip()) < 80:
            continue
        num_m = re.search(r"\[步骤\s*(\d+)\s*/", header_line)
        plan_line = None
        if num_m:
            plan_line = _find_plan_line_for_step(text, int(num_m.group(1)))
        if plan_line:
            new_span = header_line + "\n" + plan_line + "\n\n" + STEP_BODY_REDACTED_MARKER + "\n"
        else:
            new_span = header_line + "\n\n" + STEP_BODY_REDACTED_MARKER + "\n"
        return text[:start] + new_span + text[end:], True
    return text, False


def strip_thinking_to_turn_summary(text: str) -> tuple[str, bool]:
    """L3：整条 thinking 压为一行（若已为一行占位则跳过）。"""
    t = (text or "").strip()
    if not t:
        return text, False
    if t == TURN_THINKING_REDACTED_MARKER:
        return text, False
    if len(t) <= len(TURN_THINKING_REDACTED_MARKER) + 10 and "已压缩" in t:
        return text, False
    return TURN_THINKING_REDACTED_MARKER, True


def apply_one_progressive_disk_step(
    history: list[dict[str, Any]],
    *,
    session_key: str = "",
) -> tuple[bool, str | None]:
    """对 ``history`` 做一次 L1→L2→L3 中最先命中的一步。返回 ``(是否修改, action)``。"""
    for idx, msg in enumerate(history):
        if not isinstance(msg, dict) or msg.get("role") != "thinking":
            continue
        content = msg.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        old_len = len(content)
        new_c, ok = redact_first_tool_output_in_text(content)
        if ok:
            msg["content"] = new_c
            saved = old_len - len(new_c)
            _logger.debug(
                "history_progressive action=%s session_key=%s message_index=%d approx_saved_chars=%d",
                CompressionLevel.TOOL_OUTPUT.value,
                session_key,
                idx,
                max(0, saved),
            )
            return True, CompressionLevel.TOOL_OUTPUT.value
        new_c, ok = compress_first_step_span_in_text(content)
        if ok:
            msg["content"] = new_c
            saved = old_len - len(new_c)
            _logger.debug(
                "history_progressive action=%s session_key=%s message_index=%d approx_saved_chars=%d",
                CompressionLevel.STEP_BODY.value,
                session_key,
                idx,
                max(0, saved),
            )
            return True, CompressionLevel.STEP_BODY.value
        new_c, ok = strip_thinking_to_turn_summary(content)
        if ok:
            msg["content"] = new_c
            saved = old_len - len(new_c)
            _logger.debug(
                "history_progressive action=%s session_key=%s message_index=%d approx_saved_chars=%d",
                CompressionLevel.TURN_PROCESS.value,
                session_key,
                idx,
                max(0, saved),
            )
            return True, CompressionLevel.TURN_PROCESS.value
    return False, None


def run_session_history_maintenance(
    session_key: str,
    history: list[dict[str, Any]],
    *,
    tail_cap: int,
    progressive_compression: bool | None = None,
) -> None:
    """先渐进压缩（L1–L3），仍超归档/条数阈值则单次归档、单次 trim，循环直至达标或迭代上限。"""
    from miniagent.memory.history_archive import maybe_archive_old_turns, trim_history_tail_by_turns

    max_it = _maintenance_max_iters()
    prog = _progressive_enabled(progressive_compression)

    for _ in range(max_it):
        over_a = _over_archive_limits(history)
        over_t = _over_tail_cap(history, tail_cap)
        if not over_a and not over_t:
            break

        progressed = False
        if prog and (over_a or over_t):
            progressed, _action = apply_one_progressive_disk_step(history, session_key=session_key)
            if progressed:
                continue

        if over_a:
            if maybe_archive_old_turns(session_key, history):
                _logger.debug(
                    "history_progressive action=%s session_key=%s",
                    CompressionLevel.TURN_REMOVE_ARCHIVE.value,
                    session_key,
                )
                continue

        if over_t:
            if trim_history_tail_by_turns(history, tail_cap):
                _logger.debug(
                    "history_progressive action=%s session_key=%s",
                    CompressionLevel.TURN_REMOVE_TRIM.value,
                    session_key,
                )
                continue

        if not progressed:
            break


__all__ = [
    "CompressionLevel",
    "TOOL_OUTPUT_REDACTED_PLACEHOLDER",
    "STEP_BODY_REDACTED_MARKER",
    "TURN_THINKING_REDACTED_MARKER",
    "redact_first_tool_output_in_text",
    "compress_first_step_span_in_text",
    "strip_thinking_to_turn_summary",
    "apply_one_progressive_disk_step",
    "run_session_history_maintenance",
]
