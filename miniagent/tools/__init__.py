"""Mini Agent Python — 工具模块

导出所有内置工具集合，按功能分组：
- filesystem_tools: 文件/目录操作（read/write/edit/list/create/move/copy/delete）
- exec_tools: 命令执行
- web_tools: 网页抓取和搜索
- skills_tools: 技能搜索和安装
- self_opt_tools: 自我优化工具

ALL_TOOLS 字典汇总所有内置工具，供 Agent 编排层使用。
"""

from miniagent.tools.filesystem import filesystem_tools
from miniagent.tools.exec import exec_tools
from miniagent.tools.web import web_tools
from miniagent.tools.skills import skills_tools
from miniagent.tools.self_opt import self_opt_tools

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
