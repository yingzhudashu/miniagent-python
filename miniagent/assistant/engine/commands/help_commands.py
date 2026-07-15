"""由单一命令元数据和运行时状态生成 CLI/飞书帮助。"""

from __future__ import annotations

from typing import Any


def _md_escape_cell(text: str) -> str:
    """Markdown 单元格文本：去掉换行并转义管道符（``cmd_session_list`` 等 GFM 表格用）。"""
    s = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("|", "\\|").replace("\n", " ").strip()
    return s


def _md_help_section(title: str, hint: str | None, rows: list[tuple[str, str]]) -> str:
    """生成分组 Markdown：可选引用提示 + 粗体命令列表（飞书 lark_md 友好）。

    避免使用 GFM 表格（飞书不支持），改用粗体 + 列表格式，
    使 CLI Markdown 渲染和飞书 lark_md 都能正常显示。
    """
    lines: list[str] = [f"### {title}"]
    if hint:
        lines.append(f"> {hint}")
    # 使用列表格式，命令用粗体，说明紧跟其后（飞书和 CLI 都友好）
    for cmd, desc in rows:
        # 粗体命令 + 分隔符 + 说明
        lines.append(f"- **{cmd}** — {desc}")
    lines.extend(["", ""])
    return "\n".join(lines)


def _runtime_help_sections() -> list[str]:
    """返回启动、实例、会话、飞书、队列与确认控制分节。"""
    return [
        _md_help_section(
            "启动命令（在操作系统终端执行）",
            None,
            [
                ("`python -m miniagent`", "启动 CLI 模式"),
                ("`python -m miniagent --continue`", "继续上次 CLI 活跃会话"),
                ("`python -m miniagent --session <ID>`", "启动并绑定到指定会话"),
                ("`python -m miniagent --feishu`", "启动 CLI + 飞书"),
                ("`python -m miniagent --feishu --continue`", "CLI + 飞书，并继续上次会话"),
                ("`python -m miniagent --stop`", "列出实例；交互选择停止"),
                ("`python -m miniagent --stop --all`", "停止全部实例"),
                ("`python -m miniagent --stop <id>...`", "停止指定实例 ID"),
                ("`python -m miniagent --stop --state-dir <路径> <id>...`", "多状态根时指定目录后停止"),
            ],
        ),
        _md_help_section(
            "实例管理",
            None,
            [
                ("`/instance list`", "列出所有运行实例"),
                ("`/instance stop <id>`", "停止指定实例"),
            ],
        ),
        _md_help_section(
            "会话管理",
            "编号与原始 ID 均可；切换会话会同步 CLI 与已自动跟随的飞书私聊绑定。"
            "飞书群可用 `/session switch oc_xxx` 聚焦群聊会话。",
            [
                ("`/session list`", "列出所有会话"),
                ("`/session switch <编号/ID>`", "切换到指定会话（含飞书群 oc_xxx）"),
                ("`/session create <ID> [标题]`", "创建新会话，可指定标题"),
                ("`/session rename <编号/ID> <新标题>`", "重命名会话"),
                ("`/session delete <编号/ID>`", "删除会话（不可删除当前活跃会话）"),
            ],
        ),
        _md_help_section(
            "飞书控制",
            None,
            [
                ("`/feishu start`", "启动飞书 WebSocket 连接"),
                ("`/feishu stop`", "停止飞书连接"),
                ("`/feishu status`", "查看飞书运行状态"),
            ],
        ),
        _md_help_section(
            "消息队列",
            "`queue` 为默认；`preemptive` 允许新消息插队。`/queue abort` / `/abort` 取消本 `chat_id` 上经 `dispatch` / `dispatch_wait` 投递的任务，**不是** `/stop`（停实例）。飞书侧可随时发送以打断卡住的 Agent；全屏 CLI 在单轮 Agent 执行中无法再次输入命令。",
            [
                ("`/queue status`", "查看队列状态"),
                ("`/query`", "同上（短命令）"),
                ("`/queue set <模式>`", "切换 `queue` / `preemptive`"),
                ("`/queue abort`", "中止本通道队列内运行中与排队的任务；不退出进程"),
                ("`/abort`", "同上（短命令）"),
            ],
        ),
        _md_help_section(
            "确认控制",
            "规划器判定高风险操作时会暂停等待确认。以下命令不经过消息队列，直接响应暂停点。",
            [
                ("`/confirm`", "批准当前待确认的规划，继续执行"),
                ("`/adjust <内容>`", "调整内容并批准"),
                ("`/reject`", "拒绝当前规划，取消操作"),
            ],
        ),
    ]


def _feature_help_sections() -> list[str]:
    """返回答案改进、调度、自优化、知识库、统计和自测分节。"""
    return [
        _md_help_section(
            "答案改进",
            "根据质量评估建议改进上一轮答案；支持多轮改进。",
            [
                ("`/improve`", "根据质量评估建议改进上一轮答案"),
                ("`/improve --force`", "强制改进（即使质量已通过）"),
                ("`/improve --reset`", "回退到原始答案重新改进"),
                ("`/review`", "自我反驳式审查答案（迭代最多3轮）"),
            ],
        ),
        _md_help_section(
            "定时任务",
            "用 `` -- `` 分隔参数与 prompt；once 可加 ``--tz``；飞书默认仅 list/show，MINIAGENT_FEISHU_DOT_COMMANDS_FULL=1 时与 CLI 同等。",
            [
                ("`/schedule list`", "列出任务"),
                ("`/schedule show <id>`", "查看 JSON"),
                (
                    "`/schedule add ...`",
                    "interval/once（见无参 `/schedule`）；Agent 可用 manage_scheduled_task",
                ),
                ("`/schedule update <id> …`", "修改任务（语法同 add）"),
                ("`/schedule remove|enable|disable <id>`", "管理任务"),
            ],
        ),
        _md_help_section(
            "自我优化",
            "基于运行日志和代码分析生成优化提案，默认仅生成不执行。配置 auto_apply:true 可自动执行低风险提案。",
            [
                ("`/self-opt status`", "查看自我优化系统状态"),
                ("`/self-opt proposals [status]`", "列出待执行提案（可按状态过滤）"),
                ("`/self-opt show <id>`", "查看提案详情"),
                ("`/self-opt approve <id>`", "批准提案"),
                ("`/self-opt reject <id>`", "拒绝提案"),
                ("`/self-opt apply <id> [root]`", "执行已批准的提案（可选指定根目录）"),
                ("`/self-opt analyze`", "触发运行分析"),
                ("`/self-opt report [date]`", "查看分析报告（可选指定日期）"),
            ],
        ),
        _md_help_section(
            "知识库",
            "挂载本地文档供 Agent 检索；知识库目录应有 KB.yaml 或 files/ 子目录。",
            [
                ("`/kb list`", "列出已挂载的知识库"),
                ("`/kb mount <路径> [名称]`", "挂载知识库（目录或文件）"),
                ("`/kb unmount <名称>`", "卸载知识库"),
                ("`/kb search <关键词> [名称]`", "检索知识库内容"),
                ("`/kb reload [名称]`", "重新加载知识库"),
            ],
        ),
        _md_help_section(
            "工具与统计",
            None,
            [
                ("`/stats`", "查看工具调用统计"),
                ("`/status`", "查看系统运行状态"),
            ],
        ),
        _md_help_section(
            "自测命令",
            "测试样本位于 tests/evaluation/samples/；默认 mock 模式（不调用真实 LLM）。",
            [
                ("`/test run`", "运行所有测试"),
                ("`/test run <类别>`", "按类别过滤（security | prompt_injection | tool_selection | schema | regression | cost）"),
                ("`/test run <类别> <名称>`", "进一步按名称过滤（正则）"),
                ("`/test list`", "列出所有测试样本"),
                ("`/test status`", "查看最近测试结果"),
            ],
        ),
    ]


def _maintenance_help_sections(instance_id: int | None) -> list[str]:
    """返回实例停止、后台任务、配置诊断和其它命令分节。"""
    return [
        _md_help_section(
            "实例控制",
            None,
            [
                (
                    "`/stop`",
                    (
                        f"停止当前实例并退出（实例 #{instance_id}）"
                        if instance_id
                        else "停止当前实例并退出"
                    ),
                ),
            ],
        ),
        _md_help_section(
            "后台任务",
            "并行执行子任务，不污染主对话历史。",
            [
                ("`/btw start <prompt>`", "启动后台任务"),
                ("`/btw status [任务ID]`", "查看任务列表或指定任务状态"),
                ("`/btw result <id>`", "获取任务结果"),
                ("`/btw cancel <id>`", "取消任务"),
                ("`/btw clear`", "清理已完成/失败/取消的任务"),
                ("`Ctrl+T`", "快捷键查看任务列表"),
            ],
        ),
        _md_help_section(
            "配置与诊断",
            None,
            [
                (
                    "`/config [section]`",
                    "查看配置概览；指定 section 时查看该部分（如 model、paths、feishu）",
                ),
                ("`/reload-config`", "重新加载 config.user.json（配置热更新）"),
                (
                    "`/model [name]`",
                    "显示当前模型；指定 name 时切换模型",
                ),
                ("`/doctor`", "诊断安装与配置"),
            ],
        ),
        _md_help_section(
            "其他",
            None,
            [
                ("`/help`", "显示本帮助"),
                ("`/reload-skills`", "从磁盘重新加载技能（无需重启）"),
                ("`/copy`", "复制当前会话全文到剪贴板（全屏 transcript / 简易 history）"),
                ("`quit` / `exit`", "退出程序"),
            ],
        ),
    ]


def format_help_markdown(
    message_queue: Any,
    instance_id: int | None = None,
) -> str:
    """生成 `/help` 的 Markdown 正文，供 CLI 与飞书复用。"""
    header_lines = ["## Mini Agent 命令", "", f"消息队列模式：**{message_queue.mode.value}**"]
    if instance_id is not None:
        header_lines.append(f"当前实例：**#{instance_id}**")
    header_lines.extend(["", ""])
    header = "\n".join(header_lines)
    sections = [
        *_runtime_help_sections(),
        *_feature_help_sections(),
        *_maintenance_help_sections(instance_id),
    ]

    footer = "\n".join(
        [
            "> 提示：直接输入文字即可与 Agent 对话。",
            "",
        ]
    )

    return header + "\n" + "".join(sections) + footer


__all__ = ["format_help_markdown"]
