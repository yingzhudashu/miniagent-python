"""Mini Agent Python — 工具模块

导出所有内置工具集合。
"""

from src.tools.filesystem import filesystem_tools
from src.tools.exec import exec_tools
from src.tools.web import web_tools
from src.tools.skills import skills_tools
from src.tools.self_opt import self_opt_tools

# 汇总所有内置工具
ALL_TOOLS = {
    **filesystem_tools,
    **exec_tools,
    **web_tools,
    **skills_tools,
    **self_opt_tools,
}

__all__ = [
    "filesystem_tools",
    "exec_tools",
    "web_tools",
    "skills_tools",
    "self_opt_tools",
    "ALL_TOOLS",
]
