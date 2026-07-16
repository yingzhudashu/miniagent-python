"""Mini Agent Python — 统一入口

启动 Agent 后默认进入 CLI 模式。
运行时可通过 `/feishu start` 动态启用飞书连接。

用法:
    python -m miniagent --help       # 显示命令行用法
    python -m miniagent -h           # 同上
    python -m miniagent              # CLI 模式（默认）
    python -m miniagent --continue   # 继续上次会话（而非默认会话）
    python -m miniagent --no-continue  # 禁用隐式继续，使用 default 会话
    python -m miniagent --session <ID>  # 启动并绑定到指定会话
    python -m miniagent --feishu     # CLI + 飞书同时启动
    python -m miniagent --feishu --continue  # CLI + 飞书，并继续上次会话
    python -m miniagent --stop       # 列出运行中实例，交互选择停止（TTY）或见下方非交互用法
    python -m miniagent --stop --all # 停止全部运行中实例
    python -m miniagent --stop 1 2   # 停止指定实例 ID（可多个）
    python -m miniagent --stop --state-dir <路径> 1  # 多状态根时指定目录
    python -m miniagent --stop --state-dir <路径>  # 交互选择，仅列该状态根下的实例
    python -m miniagent --doctor     # 环境诊断（安装、依赖、配置、状态目录）

架构（组合根）:
- 进程级依赖由 ``bootstrap.entrypoint`` 构造唯一 ``ApplicationContainer``
- ``--feishu`` 等运行时开关由 ``engine.main`` 读取 ``sys.argv``（本模块不解析）
- CLI 经 ``run_cli_loop``；飞书经 ``FeishuRuntime`` + ``poll_server``（同进程可插拔）
- ``AssistantTurnService`` 编排 ``run_agent`` 与思考回调；会话由 ``SessionManager`` 单一数据源

文档索引见 ``docs/INDEX.md``；架构详见 ``docs/ARCHITECTURE.md``。
"""

from __future__ import annotations

import sys
from typing import Any

from miniagent.agent.types.error_prefix import ERROR_PREFIX, SUCCESS_PREFIX


def _wants_help(argv: list[str]) -> bool:
    """是否请求打印命令行用法（``--help`` / ``-h``）。"""
    return any(flag in argv for flag in ("--help", "-h"))


def _print_cli_help() -> None:
    """打印命令行用法（取自本模块文档字符串中的「用法」段）。"""
    doc = __doc__ or ""
    start = doc.find("用法:")
    end = doc.find("架构（组合根）:")
    usage = doc[start:end].strip() if start >= 0 and end > start else doc.strip()
    print("Mini Agent Python\n")
    print(usage)
    print("\n亦可通过以下方式启动（等价）:")
    print("    miniagent                  # pip 安装后的命令")
    print("    python -m miniagent.assistant.cli.cli")
    print("\n文档索引见 docs/INDEX.md；架构详见 docs/ARCHITECTURE.md。")


def _load_env() -> None:
    """加载敏感凭据（``config.user.json`` 的 secrets 段）。

    幂等；正式入口在创建 LLM 客户端前会再次加载，重复调用无害。
    """
    from miniagent.assistant.infrastructure.env_loader import load_secrets_from_project_root

    load_secrets_from_project_root()


def _argv_after_flag(argv: list[str], flag: str) -> list[str]:
    """返回 ``flag`` 之后的所有参数（``--stop`` 子命令独占行尾）。"""
    if flag not in argv:
        return []
    i = argv.index(flag)
    return argv[i + 1 :]


def _extract_stop_state_dir(tokens: list[str]) -> tuple[str | None, list[str]]:
    """从 ``--stop`` token 中解析 ``--state-dir``，返回 (路径, 剩余 token)。"""
    if not tokens:
        return None, tokens
    out: list[str] = []
    state_dir: str | None = None
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t == "--state-dir":
            if i + 1 >= len(tokens):
                raise ValueError("--state-dir 需要路径参数")
            state_dir = tokens[i + 1]
            i += 2
            continue
        out.append(t)
        i += 1
    return state_dir, out


def _instance_targets(instances: list[dict[str, Any]]) -> list[tuple[int, str]]:
    """(instance_id, state_dir) 列表，用于停止目标解析。"""
    targets: list[tuple[int, str]] = []
    for inst in instances:
        iid = inst.get("instance_id")
        sd = inst.get("state_dir")
        if iid is None or not sd:
            continue
        targets.append((int(iid), str(sd)))
    return targets


def _parse_stop_target_ids(
    tokens: list[str],
    targets: list[tuple[int, str]],
    *,
    filter_state_dir: str | None = None,
) -> tuple[list[tuple[int, str]] | None, str | None]:
    """解析 ``--stop`` 后的 token 列表。返回 ((id, state_dir) 列表, 错误信息)。"""
    if not tokens:
        return None, None

    from miniagent.assistant.infrastructure.paths import paths_equal

    scoped = targets
    if filter_state_dir is not None:
        scoped = [
            (iid, sd) for iid, sd in targets if paths_equal(sd, filter_state_dir)
        ]

    valid_ids = {iid for iid, _ in scoped}

    if tokens in (["--all"], ["all"]):
        return sorted(scoped, key=lambda x: (x[1], x[0])), None

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

    seen: set[int] = set()
    ordered_ids: list[int] = []
    for n in ids:
        if n not in seen:
            seen.add(n)
            ordered_ids.append(n)

    result: list[tuple[int, str]] = []
    for n in ordered_ids:
        matches = [(iid, sd) for iid, sd in scoped if iid == n]
        if len(matches) > 1:
            dirs = ", ".join(sorted({sd for _, sd in matches}))
            return None, f"实例 #{n} 存在于多个状态目录（{dirs}），请使用 --state-dir 指定"
        result.append(matches[0])

    return result, None


def _run_stop_command() -> int:
    """处理 ``--stop``：列出实例，按交互或非交互停止指定或全部实例。

    退出码：0 成功或无可停实例；1 非 TTY 未指定目标或部分停止失败；2 参数错误。
    """
    from miniagent.assistant.infrastructure.instance import (
        format_instances_table,
        list_instances,
        stop_instance_by_id,
    )

    instances = list_instances()
    targets = _instance_targets(instances)

    print(format_instances_table(instances))

    if not instances:
        return 0

    raw_tokens = _argv_after_flag(sys.argv, "--stop")
    try:
        filter_state_dir, tokens = _extract_stop_state_dir(raw_tokens)
    except ValueError as e:
        print(f"{ERROR_PREFIX} {e}")
        return 2

    if tokens:
        stop_targets, err = _parse_stop_target_ids(
            tokens, targets, filter_state_dir=filter_state_dir
        )
        if err:
            print(f"{ERROR_PREFIX} {err}")
            print(
                "\n用法:\n"
                "  python -m miniagent --stop                    交互选择（需在终端中运行）\n"
                "  python -m miniagent --stop --all              停止全部\n"
                "  python -m miniagent --stop <id>...            停止指定实例 ID\n"
                "  python -m miniagent --stop --state-dir <路径> <id>...  指定状态目录\n"
            )
            return 2
        assert stop_targets is not None
    else:
        if not sys.stdin.isatty():
            print(
                "ℹ️ 当前不是交互式终端。请指定要停止的实例，例如:\n"
                "  python -m miniagent --stop --all\n"
                "  python -m miniagent --stop <实例ID> [<实例ID> ...]\n"
                "  python -m miniagent --stop --state-dir <路径> <实例ID>\n"
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
            stop_targets, err = _parse_stop_target_ids(
                ["all"], targets, filter_state_dir=filter_state_dir
            )
            assert err is None
        else:
            raw_parts = [p.strip() for p in normalized.split(",") if p.strip()]
            if not raw_parts:
                print("未输入任何 ID，已取消。")
                return 0
            stop_targets, err = _parse_stop_target_ids(
                raw_parts, targets, filter_state_dir=filter_state_dir
            )
            if err:
                print(f"{ERROR_PREFIX} {err}")
                return 2
            assert stop_targets is not None

    assert stop_targets is not None
    exit_code = 0
    for iid, sd in stop_targets:
        result = stop_instance_by_id(iid, state_dir=sd)
        if result.get("success"):
            print(f"{SUCCESS_PREFIX} 实例 #{iid} 已停止 ({sd})")
        else:
            print(f"ℹ️ 实例 #{iid}: {result.get('reason', '停止失败')}")
            exit_code = 1
    return exit_code


def _bootstrap_project_paths(
    *,
    for_stop: bool = False,
    skip_continue: bool = False,
) -> None:
    """绑定项目目录与项目状态根；启动路径下单实例预检与隐式 ``--continue``。"""
    import os

    from miniagent.assistant.infrastructure.instance import (
        find_alive_instance_for_project,
        format_project_conflict_message,
    )
    from miniagent.assistant.infrastructure.paths import (
        normalize_project_dir,
        resolve_project_state_dir,
    )

    project_dir = normalize_project_dir(os.getcwd())
    os.environ["MINIAGENT_PROJECT_DIR"] = project_dir

    if not os.environ.get("MINIAGENT_PATHS_STATE_DIR", "").strip():
        os.environ["MINIAGENT_PATHS_STATE_DIR"] = resolve_project_state_dir()

    if for_stop:
        return

    existing = find_alive_instance_for_project(project_dir)
    if existing is not None:
        print(format_project_conflict_message(existing))
        raise SystemExit(2)

    if skip_continue:
        return

    if not os.environ.get("MINIAGENT_CONTINUE_SESSION", "").strip():
        os.environ["MINIAGENT_CONTINUE_SESSION"] = "1"


def _consume_session_arg() -> None:
    """解析 ``--session <ID>``，设置 ``MINIAGENT_SESSION_NAME`` 并从 argv 移除。"""
    import os

    if "--session" not in sys.argv:
        return
    i = sys.argv.index("--session")
    if i + 1 >= len(sys.argv) or sys.argv[i + 1].startswith("-"):
        print(
            f"{ERROR_PREFIX} --session 需要会话 ID 参数\n\n"
            "用法: python -m miniagent --session <ID>\n"
        )
        raise SystemExit(2)
    os.environ["MINIAGENT_SESSION_NAME"] = sys.argv[i + 1]
    del sys.argv[i : i + 2]


def _run_current_argv() -> None:
    """统一入口：解析 CLI 开关后启动正式应用入口。

    处理顺序：``--help`` / ``-h``（早退）→ 加载凭据 → ``--no-continue`` / ``--continue``
    → ``--session`` → ``--stop`` / ``--doctor``（早退）→ 绑定项目路径 → 构造并运行应用。
    """
    import os

    if _wants_help(sys.argv):
        _print_cli_help()
        raise SystemExit(0)

    _load_env()

    no_continue = "--no-continue" in sys.argv
    if no_continue:
        sys.argv.remove("--no-continue")

    explicit_continue = "--continue" in sys.argv
    if explicit_continue:
        os.environ["MINIAGENT_CONTINUE_SESSION"] = "1"
        sys.argv.remove("--continue")

    _consume_session_arg()

    if "--stop" in sys.argv:
        _bootstrap_project_paths(for_stop=True)
        try:
            code = _run_stop_command()
        except Exception as e:
            print(f"{ERROR_PREFIX} 停止失败: {e}")
            code = 1
        raise SystemExit(code)

    if "--doctor" in sys.argv:
        sys.argv.remove("--doctor")
        _bootstrap_project_paths(for_stop=True)
        from miniagent.assistant.engine.doctor import print_diagnose_report

        print_diagnose_report()
        raise SystemExit(0)

    _bootstrap_project_paths(
        skip_continue=(
            explicit_continue
            or no_continue
            or bool(os.environ.get("MINIAGENT_SESSION_NAME", "").strip())
        )
    )

    from miniagent.assistant.engine.setup_wizard import run_interactive_setup
    from miniagent.assistant.infrastructure.env_loader import load_secrets_from_project_root

    run_interactive_setup()
    load_secrets_from_project_root()
    from miniagent.assistant.app import create_assistant_application

    create_assistant_application().run()


def run_cli_boundary(argv: list[str] | None = None) -> None:
    """Implement the internal CLI boundary owned by public ``run_assistant``."""
    if argv is None:
        _run_current_argv()
        return
    previous = sys.argv
    sys.argv = [previous[0], *argv]
    try:
        _run_current_argv()
    finally:
        sys.argv = previous


if __name__ == "__main__":
    from miniagent.assistant.app import run_assistant

    run_assistant()
