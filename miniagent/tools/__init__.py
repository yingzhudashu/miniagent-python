"""Mini Agent Python — 工具模块

导出所有内置工具集合，按功能分组：
- filesystem_tools: 文件/目录操作（read/write/edit/list/create/move/copy/delete）
- exec_tools: 命令执行
- web_tools: 时间查询（get_time）；web_search/browser_extract_text/fetch_url 已移至
  ``miniagent/skills/templates/builtin-web`` skill 模板，启动时作为 skill 注册
- skills_tools: 技能搜索和安装
- data_tools: 数据处理（CSV/JSON 读写）
- feishu_im_tools: 飞书 IM / 云盘工具
- feishu_doc_tools: 飞书云文档 ``feishu_doc``
- feishu_bitable_tools: 飞书多维表格 ``feishu_bitable``

另：``session_memory.session_memory_tools`` 在 ``engine.init_subsystems`` 中单独注册，不在 ``ALL_TOOLS`` 字典内。

``cli_dispatch_tools``（``run_dot_command``）可由环境变量 ``MINIAGENT_CLI_DOT_TOOLS=0`` 在注册阶段关闭；工具参数 ``max_chars`` 可限制返回长度。

``schedule_tools``（``manage_scheduled_task``）可由 ``MINIAGENT_SCHEDULE_TOOLS=0`` 关闭注册。

ALL_TOOLS 汇总上述内置工具子集；启动时由 ``register_builtin_tools`` 写入主注册表。

沙箱与命令策略见 ``docs/SECURITY.md``；可选依赖与 Key 见根目录 ``README``。
"""

from miniagent.tools.cli_dispatch_tools import cli_dispatch_tools
from miniagent.tools.data_tools import data_tools
from miniagent.tools.exec import exec_tools
from miniagent.tools.feishu_bitable_tools import feishu_bitable_tools
from miniagent.tools.feishu_card_tools import feishu_card_tools
from miniagent.tools.feishu_doc_tools import feishu_doc_tools
from miniagent.tools.feishu_im_tools import feishu_im_tools
from miniagent.tools.filesystem import filesystem_tools
from miniagent.tools.schedule_tools import schedule_tools
from miniagent.tools.skills import skills_tools
from miniagent.tools.web import web_tools

# 汇总所有内置工具
ALL_TOOLS = {
    **filesystem_tools,
    **exec_tools,
    **web_tools,
    **skills_tools,
    **cli_dispatch_tools,
    **schedule_tools,
    **feishu_im_tools,
    **feishu_doc_tools,
    **feishu_bitable_tools,
    **feishu_card_tools,
    **data_tools,
}

__all__ = [
    "filesystem_tools",
    "exec_tools",
    "web_tools",
    "skills_tools",
    "cli_dispatch_tools",
    "schedule_tools",
    "feishu_im_tools",
    "feishu_doc_tools",
    "feishu_bitable_tools",
    "feishu_card_tools",
    "data_tools",
    "ALL_TOOLS",
]
