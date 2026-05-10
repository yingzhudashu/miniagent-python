"""运行时组合根：显式持有进程级依赖，避免散落模块全局。"""

from miniagent.runtime.context import RuntimeContext

__all__ = ["RuntimeContext"]
