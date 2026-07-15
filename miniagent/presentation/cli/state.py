"""Small typed view state kept separate from agent and session state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

TuiTheme = Literal["auto", "dark", "light"]


@dataclass(slots=True)
class TuiViewState:
    busy: bool = False
    status: str = "就绪"
    # 评估、计划和执行详情复用 thinking 通道；默认折叠会导致真实 TUI 只显示
    # 阶段标题而吞掉正文，因此必须以展开状态启动。用户仍可用 Ctrl+R 临时折叠。
    reasoning_expanded: bool = True
    theme: TuiTheme = "auto"
    queued_messages: int = 0
    input_mode: str = "单轮"

    def toggle_reasoning(self) -> bool:
        self.reasoning_expanded = not self.reasoning_expanded
        return self.reasoning_expanded


__all__ = ["TuiTheme", "TuiViewState"]
