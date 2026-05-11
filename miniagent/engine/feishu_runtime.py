"""兼容别名模块：仅重导出 :class:`FeishuRuntime`；实现与维护请在 :mod:`miniagent.engine.feishu_state` 单点修改。"""

from miniagent.engine.feishu_state import FeishuRuntime

__all__ = ["FeishuRuntime"]
