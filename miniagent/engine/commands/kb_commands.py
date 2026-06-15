"""知识库命令模块

提供 `/kb` 相关命令的执行实现（参数解析在 ``command_dispatch.py``）：
- list: 列出已挂载的知识库
- mount: 挂载知识库（目录或文件）
- unmount: 卸载知识库
- search: 检索知识库内容
- reload: 重新加载知识库

飞书 capture 路径下 ``cmd_kb_list(markdown=True)`` 会输出 GFM 表格（由 dispatch 根据
``feishu.markdown_commands`` 传入）。

使用方式：
    from miniagent.engine.commands.kb_commands import cmd_kb_list
"""

from __future__ import annotations

from miniagent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX, WARNING_PREFIX


def _md_escape_cell(text: str) -> str:
    """Markdown 表格单元格：去掉换行并转义管道符。"""
    s = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    return s.replace("|", "\\|").replace("\n", " ").strip()


def format_kb_command_usage() -> str:
    """返回 `/kb` 知识库命令的用法说明。

    Returns:
        多行用法字符串（无尾部换行）。
    """
    return (
        "知识库命令（挂载本地文档供 Agent 检索）：\n"
        "  /kb list                        列出已挂载的知识库\n"
        "  /kb mount <路径> [名称]         挂载知识库（目录或文件）\n"
        "  /kb unmount <名称>              卸载知识库\n"
        "  /kb search <关键词> [名称]      检索知识库内容\n"
        "  /kb reload [名称]               重新加载知识库\n"
        "  说明: 知识库目录应有 KB.yaml 或 files/ 子目录"
    )


def cmd_kb_list(*, markdown: bool = False) -> None:
    """列出已挂载的知识库。

    输出写入 stdout；``command_dispatch`` 在 capture 模式下会捕获打印内容。

    Args:
        markdown: True 时输出 Markdown 表格格式（飞书 capture 路径）。
    """
    from miniagent.knowledge import get_kb_registry

    registry = get_kb_registry()
    kb_list = registry.list()

    if not kb_list:
        print("📭 当前未挂载任何知识库")
        print("使用 `/kb mount <路径>` 挂载知识库")
        return

    if markdown:
        lines = [
            "## 已挂载知识库",
            "",
            "| 名称 | 条目数 | 关键词数 | 路径 |",
            "| --- | --- | --- | --- |",
        ]
        for kb in kb_list:
            name = _md_escape_cell(str(kb["name"]))
            path = _md_escape_cell(str(kb["path"]))
            lines.append(
                f"| {name} | {kb['entries']} | {kb['keywords']} | `{path}` |"
            )
        print("\n".join(lines))
        print()
        return

    print("\n📚 已挂载知识库:")
    for kb in kb_list:
        print(f"  - {kb['name']}: {kb['entries']} 条目, {kb['keywords']} 关键词")
        print(f"    路径: {kb['path']}")
    print()


def cmd_kb_mount(path: str, name: str | None = None) -> None:
    """挂载知识库。

    Args:
        path: 知识库路径（目录或文件）
        name: 可选的知识库名称
    """
    from miniagent.knowledge import mount_knowledge_base

    result = mount_knowledge_base(path, name)
    if result.get("success"):
        stats = result.get("stats", {})
        print(f"{SUCCESS_PREFIX} 已挂载知识库: {result.get('kb_name')}")
        print(f"   条目数: {stats.get('entries', 0)}, 关键词: {stats.get('keywords', 0)}")
    else:
        print(f"{ERROR_PREFIX} {result.get('message')}")


def cmd_kb_unmount(name: str) -> None:
    """卸载知识库。

    Args:
        name: 要卸载的知识库名称
    """
    from miniagent.knowledge import unmount_knowledge_base

    result = unmount_knowledge_base(name)
    if result.get("success"):
        print(f"{SUCCESS_PREFIX} {result.get('message')}")
    else:
        print(f"{ERROR_PREFIX} {result.get('message')}")


def cmd_kb_search(query: str, kb_name: str | None = None) -> None:
    """检索知识库内容。

    Args:
        query: 搜索关键词
        kb_name: 可选的限定知识库名称
    """
    if not (query or "").strip():
        print(f"{WARNING_PREFIX} 请提供搜索关键词")
        return

    from miniagent.knowledge import search_knowledge

    result = search_knowledge(query, kb_name=kb_name)
    if result:
        print(result)
    else:
        print(f"{WARNING_PREFIX} 未找到相关内容")


def cmd_kb_reload(name: str | None = None) -> None:
    """重新加载知识库。

    Args:
        name: 可选的指定知识库名称；未指定时重载全部
    """
    from miniagent.knowledge import get_kb_registry

    registry = get_kb_registry()
    result = registry.reload(name)
    if result.get("success"):
        print(f"{SUCCESS_PREFIX} {result.get('message')}")
    else:
        print(f"{ERROR_PREFIX} {result.get('message')}")


__all__ = [
    "format_kb_command_usage",
    "cmd_kb_list",
    "cmd_kb_mount",
    "cmd_kb_unmount",
    "cmd_kb_search",
    "cmd_kb_reload",
]
