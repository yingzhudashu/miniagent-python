"""运行时组合根：导出 ``RuntimeContext`` 及进程级登记辅助函数。

由 ``engine.main.unified_main``（或测试）构造后传入 ``unified_main``、命令调度与飞书 handler；
``compat.unified_entry`` 构造后会 ``set_runtime_context(ctx)``。勿在业务模块中缓存
「第二个」隐式全局上下文。

架构说明见 ``docs/ARCHITECTURE.md``（组合根与数据流）。"""

from miniagent.runtime.context import (
    RuntimeContext,
    get_runtime_context,
    reset_runtime_context_for_tests,
    set_runtime_context,
)

__all__ = [
    "RuntimeContext",
    "get_runtime_context",
    "set_runtime_context",
    "reset_runtime_context_for_tests",
]
