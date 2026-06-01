"""内置工具箱元数据（供规划阶段与 UI 展示；不依赖技能包目录）。

与 ``miniagent.tools.ALL_TOOLS`` 能力对齐；Phase 1 规划器按 toolbox id 筛选 Phase 2 可见工具。
"""

from __future__ import annotations

from miniagent.types.tool import Toolbox

BUILTIN_TOOLBOXES: list[Toolbox] = [
    Toolbox(
        id="skills_management",
        name="技能市场",
        description="搜索、安装、列出 ClawHub / 本地技能（search_skills、install_skill、list_skills）",
        keywords=["技能", "clawhub", "安装", "插件"],
    ),
    Toolbox(
        id="file_read",
        name="文件读取",
        description="读取与检索工作区内文件（含 CSV/TSV/JSON）",
        keywords=["读文件", "查看", "read"],
    ),
    Toolbox(
        id="file_write",
        name="文件写入",
        description="写入与编辑工作区内文件（含 CSV/JSON）",
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
        id="feishu",
        name="飞书",
        description="飞书 IM 消息、云文档读写、多维表格、互动消息卡片",
        keywords=["飞书", "lark", "云文档", "多维表格", "bitable", "im"],
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
    Toolbox(
        id="core",
        name="核心",
        description="时间查询（get_time）",
        keywords=["时间", "日期", "timezone"],
    ),
    Toolbox(
        id="vision",
        name="视觉理解",
        description="分析图片内容，生成图片描述（analyze_image）",
        keywords=["图片", "图像", "视觉", "vision", "看", "分析", "OCR"],
    ),
    Toolbox(
        id="knowledge",
        name="知识库",
        description="检索已挂载的知识库内容（search_knowledge、read_knowledge_file、kb_list）",
        keywords=["知识库", "KB", "文档", "检索", "挂载"],
    ),
]

__all__ = ["BUILTIN_TOOLBOXES"]
