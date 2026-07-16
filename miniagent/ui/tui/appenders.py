"""TUI transcript 追加器与纯文本快照。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from prompt_toolkit.application import get_app


@dataclass(frozen=True, slots=True)
class TranscriptAppenders:
    append_transcript: Any
    transcript_plain: Any
    append_ansi_transcript: Any


def _clear_selection_if_trimmed(
    transcript: Any,
    expected_count: int,
    clear_selection: Any,
) -> None:
    """仅在字符预算确实移除了头部内容时废弃绝对偏移选区。"""
    if len(transcript) < expected_count:
        clear_selection()


def create_transcript_appenders(
    *,
    is_valid_pt_style: Any,
    output_at_bottom: Any,
    transcript: Any,
    trim_transcript: Any,
    clear_selection: Any,
    stick_bottom: list[bool],
    snap_output_bottom: Any,
    safe_ansi: Any,
) -> TranscriptAppenders:
    """创建维护裁剪计数和粘底语义的追加闭包。"""
    _is_valid_pt_style = is_valid_pt_style
    _output_at_bottom = output_at_bottom
    _transcript = transcript
    _trim_transcript = trim_transcript
    _clear_selection = clear_selection
    _stick_bottom = stick_bottom
    _snap_output_bottom = snap_output_bottom
    _safe_ansi = safe_ansi

    def _append_transcript(style_cls: str, text: str = "", *, ansi: Any = None) -> None:
        """向 transcript 追加样式化文本；同样式尾部合并；维护粘底与长度裁剪。

        性能优化：维护累计长度计数器，避免每次遍历计算。

        **安全验证**：样式在存储前经过 _is_valid_pt_style 验证，
        无效样式替换为空字符串，防止后续渲染错误。
        """
        if not text and ansi is None:
            return
        # 安全验证样式
        if not _is_valid_pt_style(style_cls):
            style_cls = ""
        at_bottom = _output_at_bottom()
        before_count = len(_transcript)
        merged = (
            _transcript
            and isinstance(_transcript[-1], tuple)
            and len(_transcript[-1]) >= 2
            and _transcript[-1][0] == style_cls
        )
        if merged:
            st, prev = _transcript[-1]
            new_text = prev + text
            _transcript[-1] = (st, new_text)
        else:
            if ansi is not None:
                _transcript.append(ansi)
            else:
                _transcript.append((style_cls, text))
        _trim_transcript()
        expected_count = before_count if merged else before_count + 1
        _clear_selection_if_trimmed(_transcript, expected_count, _clear_selection)
        try:
            get_app().invalidate()
        except Exception:
            pass
        if at_bottom or _stick_bottom[0]:
            _snap_output_bottom()
            if at_bottom:
                _stick_bottom[0] = True
        else:
            _stick_bottom[0] = False

    def _transcript_plain() -> str:
        """将当前 transcript 转为纯文本（剥离 ANSI，用于复制等）。"""
        from miniagent.ui.tui.transcript import transcript_plain

        return transcript_plain(list(_transcript))

    def _append_ansi_transcript(ansi_obj: Any) -> None:
        """向 transcript 直接追加 ANSI 对象，含 trim/scroll 管理。

        性能优化：更新累计长度计数器。
        """
        at_bottom = _output_at_bottom()
        before_count = len(_transcript)
        _transcript.append(ansi_obj)
        _trim_transcript()
        _clear_selection_if_trimmed(_transcript, before_count + 1, _clear_selection)
        try:
            get_app().invalidate()
        except Exception:
            pass
        if at_bottom or _stick_bottom[0]:
            _snap_output_bottom()
            if at_bottom:
                _stick_bottom[0] = True
        else:
            _stick_bottom[0] = False


    return TranscriptAppenders(
        append_transcript=_append_transcript,
        transcript_plain=_transcript_plain,
        append_ansi_transcript=_append_ansi_transcript,
    )


__all__ = ["TranscriptAppenders", "create_transcript_appenders"]
