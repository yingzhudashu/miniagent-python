"""运行时组合根：导出 ``RuntimeContext``。

由 ``compat.unified_entry``（或测试）构造后传入 ``unified_main``、命令调度与飞书 handler；
勿在业务模块中缓存「第二个」隐式全局上下文。"""

from miniagent.runtime.context import RuntimeContext

__all__ = ["RuntimeContext"]
