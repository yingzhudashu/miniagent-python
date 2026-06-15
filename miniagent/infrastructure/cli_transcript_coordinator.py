"""CLI transcript 轮次协调 — 并行多 session 时保证 Q→Think→Reply 块连贯。

``parallel_sessions=true`` 且存在多路同时 mirror 时，非 live 轮次整轮缓冲，
在 ``end_turn`` 时按 ``begin_turn`` 登记顺序（FIFO）原子 flush 到 transcript。
单 active turn 时 append 立即透传，流式体验不变。
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from miniagent.infrastructure.json_config import get_config

TurnMode = Literal["live", "buffer"]
TurnSource = Literal["cli", "feishu"]


@dataclass
class _TurnState:
    session_key: str
    source: TurnSource
    mode: TurnMode
    order: int
    fragments: list[Callable[[], None]] = field(default_factory=list)


class CliTranscriptCoordinator:
    """协调 agent 轮次的 CLI transcript 写入，避免多 session 交错。"""

    def __init__(
        self,
        append_fn: Callable[[str, str], None],
        append_ansi_fn: Callable[[Any], None] | None = None,
        *,
        parallel_sessions: bool | None = None,
        on_turn_end: Callable[[str], None] | None = None,
    ) -> None:
        """Args:
            append_fn: 写入 transcript 的 ``(style_cls, text)`` 回调。
            append_ansi_fn: 可选 ANSI 对象写入回调。
            parallel_sessions: 是否启用多 session 缓冲；``None`` 时读 ``agent.parallel_sessions``。
            on_turn_end: 轮次结束通知（在锁外调用）。
        """
        if parallel_sessions is None:
            parallel_sessions = bool(get_config("agent.parallel_sessions", True))
        self._append_fn = append_fn
        self._append_ansi_fn = append_ansi_fn
        self._parallel_sessions = parallel_sessions
        self._on_turn_end = on_turn_end
        self._lock = threading.Lock()
        self._turns: dict[str, _TurnState] = {}
        self._order_counter = 0
        self._live_session_key: str | None = None
        self._completed_buffered: deque[_TurnState] = deque()

    @staticmethod
    def _norm_key(session_key: str) -> str:
        return (session_key or "").strip() or "default"

    @property
    def active_turn_count(self) -> int:
        """当前尚未 ``end_turn`` 的活跃轮次数。"""
        with self._lock:
            return len(self._turns)

    def _write_policy(self, sk: str) -> Literal["direct", "buffer", "drop"]:
        """决定写入策略：直写、缓冲或丢弃（未登记且有其他 active turn）。"""
        if not self._parallel_sessions:
            return "direct"
        turn = self._turns.get(sk)
        if turn is not None:
            return "direct" if turn.mode == "live" else "buffer"
        if not self._turns:
            return "direct"
        return "drop"

    def is_live(self, session_key: str) -> bool:
        """该 session 当前轮次是否 live 直写（流式不缓冲）。"""
        if not self._parallel_sessions:
            return True
        with self._lock:
            return self._write_policy(self._norm_key(session_key)) == "direct"

    def begin_turn(self, session_key: str, *, source: TurnSource = "cli") -> None:
        """登记新轮次；并行模式下首个轮次为 live，其余 buffer 至 ``end_turn``。"""
        sk = self._norm_key(session_key)
        with self._lock:
            if sk in self._turns:
                return
            if not self._parallel_sessions:
                self._turns[sk] = _TurnState(
                    session_key=sk, source=source, mode="live", order=self._order_counter
                )
                self._order_counter += 1
                return

            active = len(self._turns)
            if active == 0:
                mode: TurnMode = "live"
                self._live_session_key = sk
            elif self._live_session_key == sk:
                mode = "live"
            else:
                mode = "buffer"

            self._turns[sk] = _TurnState(
                session_key=sk, source=source, mode=mode, order=self._order_counter
            )
            self._order_counter += 1

    def _append_direct(self, style_cls: str, text: str = "") -> None:
        self._append_fn(style_cls, text)

    def _append_ansi_direct(self, ansi_obj: Any) -> None:
        if self._append_ansi_fn is not None:
            self._append_ansi_fn(ansi_obj)

    def append(self, session_key: str, style_cls: str, text: str = "") -> None:
        """按当前写入策略追加 transcript 片段（直写 / 缓冲 / 丢弃）。"""
        sk = self._norm_key(session_key)
        if not self._parallel_sessions:
            self._append_direct(style_cls, text)
            return
        with self._lock:
            policy = self._write_policy(sk)
            if policy == "drop":
                return
            if policy == "direct":
                self._append_direct(style_cls, text)
            else:
                turn = self._turns[sk]
                turn.fragments.append(lambda s=style_cls, t=text: self._append_direct(s, t))

    def append_ansi(self, session_key: str, ansi_obj: Any) -> None:
        """与 :meth:`append` 相同策略，写入 ANSI 渲染对象。"""
        sk = self._norm_key(session_key)
        if not self._parallel_sessions or self._append_ansi_fn is None:
            self._append_ansi_direct(ansi_obj)
            return
        with self._lock:
            policy = self._write_policy(sk)
            if policy == "drop":
                return
            if policy == "direct":
                self._append_ansi_direct(ansi_obj)
            else:
                turn = self._turns[sk]
                turn.fragments.append(lambda o=ansi_obj: self._append_ansi_direct(o))

    def defer(self, session_key: str, fn: Callable[[], None]) -> None:
        """延迟执行写入（用于 thinking sink 等复杂路径）。"""
        sk = self._norm_key(session_key)
        if not self._parallel_sessions:
            fn()
            return
        with self._lock:
            policy = self._write_policy(sk)
            if policy == "drop":
                return
            if policy == "direct":
                fn()
            else:
                turn = self._turns[sk]
                turn.fragments.append(fn)

    def make_session_append(self, session_key: str) -> Callable[[str, str], None]:
        """返回绑定 ``session_key`` 的 ``append`` 闭包，供引擎回调使用。"""

        def _append(style_cls: str, text: str = "") -> None:
            self.append(session_key, style_cls, text)

        return _append

    def make_session_append_ansi(self, session_key: str) -> Callable[[Any], None]:
        """返回绑定 ``session_key`` 的 ``append_ansi`` 闭包。"""

        def _append_ansi(ansi_obj: Any) -> None:
            self.append_ansi(session_key, ansi_obj)

        return _append_ansi

    def _flush_turn(self, turn: _TurnState) -> None:
        for fn in turn.fragments:
            fn()
        turn.fragments.clear()

    def _drain_completed_buffered(self) -> None:
        """按 begin_turn 登记顺序 flush 已完成且前面无未结束轮次的缓冲。"""
        if not self._completed_buffered:
            return
        min_active = min((t.order for t in self._turns.values()), default=self._order_counter + 1)
        while self._completed_buffered and self._completed_buffered[0].order < min_active:
            turn = self._completed_buffered.popleft()
            self._flush_turn(turn)

    def _notify_turn_end(self, sk: str) -> None:
        if self._on_turn_end is not None:
            self._on_turn_end(sk)

    def end_turn(self, session_key: str) -> None:
        """结束轮次；buffer 模式在此按 FIFO 顺序 flush 到 transcript。"""
        sk = self._norm_key(session_key)
        notify = False
        with self._lock:
            turn = self._turns.pop(sk, None)
            if turn is None:
                return
            notify = True
            if turn.mode == "live":
                if self._live_session_key == sk:
                    self._live_session_key = None
                self._drain_completed_buffered()
            else:
                self._completed_buffered.append(turn)
                self._completed_buffered = deque(
                    sorted(self._completed_buffered, key=lambda t: t.order)
                )
                self._drain_completed_buffered()
        if notify:
            self._notify_turn_end(sk)


__all__ = ["CliTranscriptCoordinator"]
