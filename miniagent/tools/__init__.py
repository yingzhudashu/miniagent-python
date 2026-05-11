"""Mini Agent Python — 工具模块

导出所有内置工具集合，按功能分组：
- filesystem_tools: 文件/目录操作（read/write/edit/list/create/move/copy/delete）
- exec_tools: 命令执行
- web_tools: Tavily 搜索（web_search）、Playwright 正文抽取（browser_extract_text）、HTTP 抓取（fetch_url）、时间（get_time）
- skills_tools: 技能搜索和安装
- self_opt_tools: 自我优化工具（可由 MINIAGENT_SELF_OPT_TOOLS=0 在注册阶段跳过）
- git_readonly_tools: 只读 git status/diff

另：``session_memory.session_memory_tools`` 在 ``engine.init_subsystems`` 中单独注册，不在 ``ALL_TOOLS`` 字典内。

``cli_dispatch_tools``（``run_dot_command``）可由环境变量 ``MINIAGENT_CLI_DOT_TOOLS=0`` 在注册阶段关闭；工具参数 ``max_chars`` 可限制返回长度。

``schedule_tools``（``manage_scheduled_task``）可由 ``MINIAGENT_SCHEDULE_TOOLS=0`` 关闭注册。

ALL_TOOLS 汇总上述内置工具子集；启动时由 ``register_builtin_tools`` 写入主注册表。
"""

from miniagent.tools.filesystem import filesystem_tools
from miniagent.tools.exec import exec_tools
from miniagent.tools.web import web_tools
from miniagent.tools.skills import skills_tools
from miniagent.tools.self_opt import self_opt_tools
from miniagent.tools.git_readonly import git_readonly_tools
from miniagent.tools.cli_dispatch_tools import cli_dispatch_tools
from miniagent.tools.schedule_tools import schedule_tools

# 汇总所有内置工具
ALL_TOOLS = {
    **filesystem_tools,
    **exec_tools,
    **web_tools,
    **skills_tools,
    **self_opt_tools,
    **git_readonly_tools,
    **cli_dispatch_tools,
    **schedule_tools,
}

__all__ = [
    "filesystem_tools",
    "exec_tools",
    "web_tools",
    "skills_tools",
    "self_opt_tools",
    "git_readonly_tools",
    "cli_dispatch_tools",
    "schedule_tools",
    "ALL_TOOLS",
]
