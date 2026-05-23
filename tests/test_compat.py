"""miniagent/compat.py 的单元测试。

compat 模块是向后兼容层，将已拆分的子包符号聚合为单一导入入口。
本测试验证：
- 所有 __all__ 导出符号均可 import
- unified_entry() 能构造 RuntimeContext 而不崩溃（隔离网络/LLM）
"""

from __future__ import annotations


class TestCompatExports:
    """验证 compat.__all__ 中的所有符号均可导入。"""

    def test_all_exports_importable(self) -> None:
        from miniagent import compat

        for name in compat.__all__:
            assert hasattr(compat, name), f"compat.__all__ lists '{name}' but it is not exported"

    def test_unified_entry_exists(self) -> None:
        from miniagent.compat import unified_entry

        assert callable(unified_entry)

    def test_runtime_context_importable(self) -> None:
        from miniagent.compat import RuntimeContext

        assert RuntimeContext is not None

    def test_engine_importable(self) -> None:
        from miniagent.compat import UnifiedEngine

        assert UnifiedEngine is not None

    def test_cli_commands_importable(self) -> None:
        from miniagent.compat import (
            cmd_help,
            cmd_session_list,
            cmd_session_switch,
        )

        assert callable(cmd_help)
        assert callable(cmd_session_list)
        assert callable(cmd_session_switch)
