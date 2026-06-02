"""Mini Agent Python — 统一入口

启动 Agent 后默认进入 CLI 模式。
运行时可通过 `.feishu start` 动态启用飞书连接。

用法:
    python -m miniagent              # CLI 模式（默认）
    python -m miniagent --continue   # 继续上次会话（而非默认会话）
    python -m miniagent --feishu     # CLI + 飞书同时启动
    python -m miniagent --stop       # 列出运行中实例，交互选择停止（TTY）或见下方非交互用法
    python -m miniagent --stop --all # 廜止全部运行中实例
    python -m miniagent --stop 1 2   # 停止指定实例 ID（可多个）

架构（组合根）:
- 进程级依赖由 ``engine.main.unified_main`` 构造 ``RuntimeContext``（registry、monitor、engine、队列、路由等）
- CLI 经 ``run_cli_loop``；飞书经 ``FeishuRuntime`` + ``poll_server``（同进程可插拔）
- ``UnifiedEngine`` 编排 ``run_agent`` 与思考回调；会话由 ``SessionManager`` 单一数据源

文档索引见 ``docs/INDEX.md``；架构详见 ``docs/ARCHITECTURE.md``。
"""

from __future__ import annotations

import sys


def _load_env():
    """加载敏感凭据（从config.user.json的secrets部分）。"""
    from miniagent.infrastructure.env_loader import load_secrets_from_project_root

    load_secrets_from_project_root()


def _argv_after_flag(argv: list[str], flag: str) -> list[str]:
    """返回 ``flag`` 之后的所有参数（``--stop`` 子命令独占行尾）。"""
    if flag not in argv:
        return []
    i = argv.index(flag)
    return argv[i + 1 :]


def _parse_stop_target_ids(
    tokens: list[str],
    valid_ids: set[int],
) -> tuple[list[int] | None, str | None]:
    """解析 ``--stop`` 后的 token 列表。返回 (id 列表, 错误信息)。"""
    if not tokens:
        return None, None
    if tokens in (["--all"], ["all"]):
        return sorted(valid_ids), None
    ids: list[int] = []
    for t in tokens:
        if t.startswith("-"):
            return None, f"无法解析的参数: {t}"
        try:
            n = int(t, 10)
        except ValueError:
            return None, f"不是有效的实例 ID: {t!r}"
        if n not in valid_ids:
            return None, f"实例 #{n} 不在当前运行列表中"
        ids.append(n)
    # 去重并保持顺序
    seen: set[int] = set()
    ordered: list[int] = []
    for n in ids:
        if n not in seen:
            seen.add(n)
            ordered.append(n)
    return ordered, None


def _run_stop_command() -> int:
    """处理 ``--stop``：列出实例，按交互或非交互停止指定或全部实例。"""
    from miniagent.infrastructure.instance import (
        format_instances_table,
        list_instances,
        stop_instance_by_id,
    )

    instances = list_instances()
    valid_ids = {int(i["instance_id"]) for i in instances if i.get("instance_id") is not None}

    print(format_instances_table(instances))

    if not instances:
        return 0

    tokens = _argv_after_flag(sys.argv, "--stop")

    if tokens:
        ids, err = _parse_stop_target_ids(tokens, valid_ids)
        if err:
            print(f"❌ {err}")
            print(
                "\n用法:\n"
                "  python -m miniagent --stop           交互选择（需在终端中运行）\n"
                "  python -m miniagent --stop --all     停止全部\n"
                "  python -m miniagent --stop <id>...   停止指定实例 ID\n"
            )
            return 2
        assert ids is not None
    else:
        if not sys.stdin.isatty():
            print(
                "ℹ️ 当前不是交互式终端。请指定要停止的实例，例如:\n"
                "  python -m miniagent --stop --all\n"
                "  python -m miniagent --stop <实例ID> [<实例ID> ...]\n"
            )
            return 1
        print(
            "请输入要停止的实例 ID（见上表「ID」列，多个用英文逗号分隔），\n"
            "  all 或 * — 停止全部\n"
            "  q — 取消\n"
            "> ",
            end="",
            flush=True,
        )
        try:
            line = input().strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。")
            return 1
        if not line or line.lower() in ("q", "quit", "exit"):
            print("已取消。")
            return 0
        normalized = line.replace("，", ",").replace("*", "all")
        if normalized.lower() in ("all",):
            ids = sorted(valid_ids)
        else:
            raw_parts = [p.strip() for p in normalized.split(",") if p.strip()]
            if not raw_parts:
                print("未输入任何 ID，已取消。")
                return 0
            ids, err = _parse_stop_target_ids(raw_parts, valid_ids)
            if err:
                print(f"❌ {err}")
                return 2
            assert ids is not None

    exit_code = 0
    for iid in ids:
        result = stop_instance_by_id(iid)
        if result.get("success"):
            print(f"✅ 实例 #{iid} 已停止")
        else:
            print(f"ℹ️ 实例 #{iid}: {result.get('reason', '停止失败')}")
            exit_code = 1
    return exit_code


def main():
    """统一入口 — 委托 ``compat.unified_entry`` 构造 RuntimeContext 后启动 ``unified_main``。"""
    import os

    _load_env()

    # --continue: 继续上次会话而非默认会话
    if "--continue" in sys.argv:
        os.environ["MINIAGENT_CONTINUE_SESSION"] = "1"
        sys.argv.remove("--continue")

    if "--stop" in sys.argv:
        try:
            code = _run_stop_command()
        except Exception as e:
            print(f"❌ 停止失败: {e}")
            code = 1
        raise SystemExit(code)

    from miniagent.compat import unified_entry

    unified_entry()


if __name__ == "__main__":
    main()
