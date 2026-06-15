"""Mini Agent Python — 工具模块

导出所有内置工具集合，按功能分组：
- filesystem_tools: 文件/目录操作（read/write/edit/list/create/move/copy/delete）
- exec_tools: 命令执行
- core_tools: 核心功能（get_time）- 从 web.py 重命名
- skills_tools: 技能搜索和安装（含 check_app_availability）
- data_tools: 数据处理（CSV/JSON 读写）
- feishu_im_tools: 飞书 IM / 云盘工具
- feishu_doc_tools: 飞书云文档 ``feishu_doc``
- feishu_bitable_tools: 飞书多维表格 ``feishu_bitable``
- feishu_card_tools: 飞书卡片工具
- vision_tools: 视觉理解（analyze_image）
- knowledge_tools: 知识库检索（search_knowledge、read_knowledge_file、kb_list）
- session_memory_tools: 会话记忆工具（read_session_diary、search_session_diary）
- cli_dispatch_tools: CLI 点命令（run_dot_command）
- schedule_tools: 定时任务（manage_scheduled_task）

``cli_dispatch_tools``（``run_dot_command``）由 ``cli.dot_tools_enabled`` 控制注册。

``schedule_tools``（``manage_scheduled_task``）由 ``scheduled_tools.enabled`` 控制注册。

ALL_TOOLS 汇总上述内置工具（约 40+ 个）；启动时由 ``register_builtin_tools`` 写入主注册表。

**权限与注册开关**：

+-------------------------------+------------------------------------------+
| 配置键                        | 影响                                     |
+===============================+==========================================+
| ``cli.dot_tools_enabled``     | ``run_dot_command``                      |
| ``scheduled_tools.enabled``   | ``manage_scheduled_task``                |
| ``feishu.tools_explicit/auto``| 飞书扩展工具（IM/文档/多维表格/卡片）    |
+-------------------------------+------------------------------------------+

``ToolDefinition.permission=require-confirm`` 的工具在 executor 中经 ``ConfirmationChannel`` 确认后执行。
路径类工具不受 ``ToolContext.permission`` 影响，统一走 ``path_utils.resolve_path_for_tool``。

重构说明：
- web.py 重命名为 core_tools.py（仅保留 get_time）
- check_app_availability 合并到 skills.py
- 使用 ToolBuilder 简化工具定义
"""

from miniagent.tools.cli_dispatch_tools import cli_dispatch_tools
from miniagent.tools.core_tools import core_tools
from miniagent.tools.data_tools import data_tools
from miniagent.tools.exec import exec_tools
from miniagent.tools.feishu_bitable_tools import feishu_bitable_tools
from miniagent.tools.feishu_card_tools import feishu_card_tools
from miniagent.tools.feishu_doc_tools import feishu_doc_tools
from miniagent.tools.feishu_im_tools import feishu_im_tools
from miniagent.tools.filesystem import filesystem_tools
from miniagent.tools.html_upload import (
    cleanup_html_files_tool,
    list_html_files_tool,
    upload_html_tool,
)
from miniagent.tools.knowledge_tools import knowledge_tools
from miniagent.tools.schedule_tools import schedule_tools
from miniagent.tools.session_memory import session_memory_tools
from miniagent.tools.skills import skills_tools
from miniagent.tools.vision import vision_tools

# HTML 上传工具集合
html_upload_tools = {
    "upload_html": upload_html_tool,
    "list_html_files": list_html_files_tool,
    "cleanup_html_files": cleanup_html_files_tool,
}

# 汇总所有内置工具
ALL_TOOLS = {
    **filesystem_tools,
    **exec_tools,
    **core_tools,
    **skills_tools,
    **cli_dispatch_tools,
    **schedule_tools,
    **feishu_im_tools,
    **feishu_doc_tools,
    **feishu_bitable_tools,
    **feishu_card_tools,
    **data_tools,
    **vision_tools,
    **knowledge_tools,
    **session_memory_tools,
    **html_upload_tools,
}

__all__ = [
    "filesystem_tools",
    "exec_tools",
    "core_tools",
    "skills_tools",
    "cli_dispatch_tools",
    "schedule_tools",
    "feishu_im_tools",
    "feishu_doc_tools",
    "feishu_bitable_tools",
    "feishu_card_tools",
    "data_tools",
    "vision_tools",
    "knowledge_tools",
    "session_memory_tools",
    "html_upload_tools",
    "ALL_TOOLS",
]