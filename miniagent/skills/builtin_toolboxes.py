"""内置工具箱元数据（供规划阶段与 UI 展示；不依赖技能包目录）。"""

from __future__ import annotations

from miniagent.types.tool import Toolbox

BUILTIN_TOOLBOXES: list[Toolbox] = [
    Toolbox(
        id="web",
        name="联网与网页",
        description=(
            "Tavily 搜索（web_search）、无头浏览器正文抽取（browser_extract_text）、"
            "HTTP 抓取（fetch_url）；事实与天气类任务优先选本工具箱"
        ),
        keywords=["搜索", "网页", "天气", "新闻", "tavily", "浏览器", "http"],
    ),
    Toolbox(
        id="skills_management",
        name="技能市场",
        description="搜索、安装、列出 ClawHub / 本地技能（search_skills、install_skill、list_skills）",
        keywords=["技能", "clawhub", "安装", "插件"],
    ),
    Toolbox(
        id="file_read",
        name="文件读取",
        description="读取与检索工作区内文件",
        keywords=["读文件", "查看", "read"],
    ),
    Toolbox(
        id="file_write",
        name="文件写入",
        description="写入与编辑工作区内文件",
        keywords=["写文件", "保存", "编辑"],
    ),
    Toolbox(
        id="dir_ops",
        name="目录操作",
        description="列出、创建、移动、复制、删除目录与路径操作",
        keywords=["目录", "文件夹", "ls", "mkdir"],
    ),
    Toolbox(
        id="exec",
        name="命令执行",
        description="在沙箱工作区内执行 shell 命令",
        keywords=["终端", "shell", "命令", "bash"],
    ),
    Toolbox(
        id="self_optimization",
        name="自我优化",
        description="代码自检、外部调研与变更提案（高风险工具需谨慎）",
        keywords=["优化", "重构", "架构"],
    ),
    Toolbox(
        id="version_control",
        name="版本控制",
        description="只读 Git 状态与差异（git_status、git_diff）",
        keywords=["git", "diff", "status", "commit", "版本"],
    ),
    Toolbox(
        id="mcp",
        name="MCP 工具",
        description="通过 MINIAGENT_MCP_STDIO 接入的外部 MCP 工具",
        keywords=["mcp", "扩展"],
    ),
    Toolbox(
        id="miniagent_shell",
        name="MiniAgent 点命令",
        description=(
            "进程内点命令（run_dot_command）与结构化定时任务（manage_scheduled_task）；"
            "含 .help、.status、.session、.schedule 等"
        ),
        keywords=["点命令", "session", "queue", "schedule", "定时", "help", "status", "miniagent"],
    ),
]

__all__ = ["BUILTIN_TOOLBOXES"]
