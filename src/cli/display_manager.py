"""Mini Agent Python — CLI 显示管理器

解决 CLI 输出稳定性问题：
1. 不重复输出相同内容
2. 新输出不影响历史显示
3. 执行期间显示实时进度
4. 清晰分离：历史对话 / 当前执行 / 输入提示符

使用方式：
    dm = DisplayManager()
    dm.print_welcome(...)          # 启动欢迎
    dm.show_user_input("...")      # 显示用户问题
    dm.show_thinking()             # LLM 思考中
    dm.show_tool_call("read_file", {"path": "..."})  # 工具执行
    dm.show_tool_result("✅ read_file: 3 行")
    dm.show_reply("你好！我是...")  # 最终回复
    dm.prompt()                    # 重绘输入提示符
"""

from __future__ import annotations

import sys
import time
from typing import Any

# ─── ANSI 转义码 ──────────────────────────────────────────

# 颜色
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_BLUE = "\033[34m"
_CYAN = "\033[36m"
_RED = "\033[31m"
_MAGENTA = "\033[35m"
_WHITE = "\033[37m"

# 背景
_BG_BLUE = "\033[44m"
_BG_GREEN = "\033[42m"
_BG_RED = "\033[41m"
_BG_YELLOW = "\033[43m"

# 光标控制
_CURSOR_UP = "\033[1A"
_CURSOR_HOME = "\033[0G"
_CLEAR_LINE = "\033[2K"
_CLEAR_REST = "\033[0J"
_SAVE_CURSOR = "\033[s"
_RESTORE_CURSOR = "\033[u"
_HIDE_CURSOR = "\033[?25l"
_SHOW_CURSOR = "\033[?25h"

# ─── 状态指示器 ───────────────────────────────────────────

_SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


class DisplayManager:
    """CLI 显示管理器

    管理终端输出，确保：
    - 历史输出不被新输出覆盖
    - 相同内容不重复打印
    - 执行期间有实时进度指示
    - 输入提示符始终可用
    """

    def __init__(self, prompt: str = "> ", color: bool = True) -> None:
        """初始化显示管理器

        Args:
            prompt: 输入提示符
            color: 是否启用颜色（Windows 旧终端可能不支持）
        """
        self._prompt = prompt
        self._color = color and sys.stdout.isatty()
        self._busy = False
        self._last_spinner_idx = 0
        self._last_spinner_time = 0.0
        self._seen_messages: set[str] = set()
        self._turn_count = 0

        # 检测 Windows 旧终端（不支持 ANSI）
        if sys.platform == "win32" and not sys.stdout.isatty():
            self._color = False

    # ── 内部辅助 ──────────────────────────────────────────

    def _c(self, text: str, *codes: str) -> str:
        """给文字上色"""
        if not self._color:
            return text
        return "".join(codes) + text + _RESET

    def _print(self, text: str, dedup: bool = True) -> None:
        """安全打印（可选去重）"""
        if dedup:
            key = text.strip()
            if key in self._seen_messages:
                return
            self._seen_messages.add(key)
            # 限制去重缓存大小
            if len(self._seen_messages) > 500:
                self._seen_messages = set(list(self._seen_messages)[-200:])
        print(text)

    def _clear_current(self) -> None:
        """清除当前行并回到行首"""
        if self._color:
            sys.stdout.write(f"{_CLEAR_LINE}{_CURSOR_HOME}")
            sys.stdout.flush()

    # ── 公共 API ──────────────────────────────────────────

    def reset(self) -> None:
        """重置显示状态（新会话或清屏时调用）"""
        self._seen_messages.clear()
        self._turn_count = 0

    def separator(self, char: str = "─", width: int = 60) -> None:
        """输出分隔线"""
        self._print(char * width, dedup=False)

    # ── 欢迎信息 ─────────────────────────────────────────

    def print_welcome(
        self,
        version: str,
        model: str,
        profile: str,
        workspace: str,
        tools: list[str],
        toolboxes: list[str] | None = None,
        skills: list[str] | None = None,
    ) -> None:
        """显示启动欢迎信息（紧凑型）"""
        header = self._c(f"🤖 Mini Agent v{version}", _BOLD, _GREEN)
        self._print(header, dedup=False)

        info_lines = [
            f"📡 {self._c(model, _CYAN)} | {profile}",
            f"📂 {workspace}",
        ]
        if toolboxes:
            info_lines.append(f"🧰 {', '.join(toolboxes)}")

        # 工具列表：超过 15 个折叠显示
        if len(tools) > 15:
            info_lines.append(f"🔧 {', '.join(tools[:12])} ... (+{len(tools) - 12})")
        else:
            info_lines.append(f"🔧 {', '.join(tools)}")

        if skills:
            info_lines.append(f"🎯 {', '.join(skills)}")

        for line in info_lines:
            self._print(f"  {line}", dedup=False)

        self._print(f"  {self._c('💡 输入问题或 quit 退出 | .stats .skills .sessions .profile .help', _DIM)}", dedup=False)
        self.separator()

    # ── 用户输入 ─────────────────────────────────────────

    def show_user_input(self, text: str) -> None:
        """显示用户输入内容（到历史流）"""
        self._turn_count += 1
        turn_label = self._c(f"━━━ Turn {self._turn_count} ━━━", _DIM)
        self._print(f"\n{turn_label}", dedup=False)
        self._print(f"  {self._c('👤 You', _BOLD, _BLUE)} {text}", dedup=False)

    # ── 执行状态 ─────────────────────────────────────────

    def show_thinking(self) -> None:
        """显示 LLM 思考中"""
        self._busy = True
        self._last_spinner_idx = 0
        self._last_spinner_time = time.monotonic()
        indicator = self._c("⠋", _MAGENTA)
        self._print(f"  {indicator} {self._c('思考中...', _DIM)}")

    def show_thinking_content(self, text: str) -> None:
        """显示 LLM 思考内容"""
        self._busy = True
        # 多行思考内容，首行标注，后续缩进
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if i == 0:
                self._print(f"  {self._c('💭', _MAGENTA)} {self._c(line, _DIM)}")
            else:
                self._print(f"     {self._c(line, _DIM)}")

    def update_spinner(self) -> None:
        """更新旋转指示器（在循环中定期调用）"""
        if not self._busy or not self._color:
            return
        now = time.monotonic()
        if now - self._last_spinner_time < 0.1:
            return  # 限制刷新频率
        self._last_spinner_time = now
        self._last_spinner_idx = (self._last_spinner_idx + 1) % len(_SPINNER)
        # 上一行替换
        sys.stdout.write(f"{_CURSOR_UP}{_CLEAR_LINE}{_CURSOR_HOME}")
        indicator = _SPINNER[self._last_spinner_idx]
        sys.stdout.write(f"  {self._c(indicator, _MAGENTA)} {self._c('思考中...', _DIM)}\n")
        sys.stdout.flush()

    def show_tool_call(self, tool_name: str, args: dict[str, Any] | None = None) -> None:
        """显示工具调用开始"""
        self._busy = True
        args_str = ""
        if args:
            # 只显示关键参数，避免过长
            brief = {}
            for k, v in list(args.items())[:2]:
                sv = str(v)
                brief[k] = sv if len(sv) <= 40 else sv[:37] + "..."
            if brief:
                args_str = f" ({', '.join(f'{k}={v}' for k, v in brief.items())})"
        indicator = self._c("🔧", _YELLOW)
        self._print(f"  {indicator} {self._c(tool_name, _YELLOW)}{args_str}")

    def show_tool_result(self, tool_name: str, success: bool, preview: str = "") -> None:
        """显示工具执行结果"""
        icon = "✅" if success else "❌"
        color = _GREEN if success else _RED
        preview_str = f" — {preview[:80]}" if preview else ""
        self._print(f"     {icon} {self._c(tool_name, color)}{preview_str}")

    def show_plan(self, summary: str) -> None:
        """显示执行计划"""
        self._print(f"  {self._c('📋', _BLUE)} {self._c(summary, _BOLD)}")

    def show_error(self, message: str) -> None:
        """显示错误信息"""
        self._print(f"  {self._c('❌', _RED)} {self._c(message, _RED)}")

    def show_warning(self, message: str) -> None:
        """显示警告信息"""
        self._print(f"  {self._c('⚠️', _YELLOW)} {message}")

    def show_info(self, message: str) -> None:
        """显示一般信息"""
        self._print(f"  {self._c('ℹ️', _CYAN)} {message}")

    # ── 最终回复 ─────────────────────────────────────────

    def show_reply(self, reply: str, elapsed_ms: float = 0) -> None:
        """显示 Agent 最终回复"""
        self._busy = False
        time_str = f" ({elapsed_ms:.0f}ms)" if elapsed_ms > 0 else ""
        header = self._c(f"🦾 Agent{time_str}", _BOLD, _GREEN)
        self._print(f"\n{header}", dedup=False)

        # 回复按行输出，保持缩进
        for line in reply.split("\n"):
            self._print(f"  {line}", dedup=False)
        self._print("")  # 空行分隔

    def show_command_result(self, title: str, content: str) -> None:
        """显示内置命令执行结果"""
        self._print(f"\n{self._c(title, _BOLD, _CYAN)}", dedup=False)
        for line in content.split("\n"):
            self._print(f"  {line}", dedup=False)
        self._print("")

    # ── 输入提示符 ───────────────────────────────────────

    def prompt(self) -> str:
        """显示输入提示符并读取用户输入

        Returns:
            用户输入的文本
        """
        self._busy = False
        try:
            if self._color:
                # 彩色提示符
                prompt_text = f"\n{_BOLD}{_GREEN}{self._prompt}{_RESET}"
            else:
                prompt_text = f"\n{self._prompt}"
            return input(prompt_text)
        except (EOFError, KeyboardInterrupt):
            return ""

    # ── 退出 ─────────────────────────────────────────────

    def farewell(self, report: str = "") -> None:
        """显示退出信息"""
        self._print(f"\n{self._c('👋 bye', _BOLD, _GREEN)}")
        if report:
            self._print(report)
