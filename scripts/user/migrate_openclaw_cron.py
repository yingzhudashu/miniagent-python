"""迁移 OpenClaw 定时任务到 miniagent（一次性脚本，不入库）。

用法:
    python scripts/user/migrate_openclaw_cron.py              # 预览（仅启用的任务）
    python scripts/user/migrate_openclaw_cron.py --apply      # 写入启用的任务
    python scripts/user/migrate_openclaw_cron.py --all         # 包含已禁用的任务
    python scripts/user/migrate_openclaw_cron.py --apply --append  # 追加到已有 tasks.json

说明:
- 仅处理 payload.kind="agentTurn" 的任务
- 默认只迁移 enabled=true 的任务
- delivery.channel="feishu" → session.feishu_chat_id
- 输出到 workspaces/scheduled_tasks/tasks.json（运行时目录，不入库）
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

OPENCLAW_JOBS = Path.home() / ".openclaw" / "cron" / "jobs.json"
MINIAGENT_ROOT = Path(__file__).resolve().parent.parent.parent
TASKS_JSON = MINIAGENT_ROOT / "workspaces" / "scheduled_tasks" / "tasks.json"


def convert_task(oc_job: dict) -> dict:
    """将单条 OpenClaw job 转为 miniagent tasks.json 格式。"""
    sched = oc_job.get("schedule", {})
    delivery = oc_job.get("delivery", {})
    payload = oc_job.get("payload", {})

    feishu_chat_id = None
    to = delivery.get("to", "")
    if to.startswith("chat:"):
        feishu_chat_id = to[5:]

    session_target = oc_job.get("sessionTarget", "primary")
    if feishu_chat_id:
        mode = "fixed"
    elif session_target == "isolated":
        mode = "ephemeral"
    else:
        mode = "primary"

    return {
        "id": str(uuid.uuid4())[:8],
        "name": oc_job.get("name", "unnamed"),
        "prompt": payload.get("message", ""),
        "enabled": oc_job.get("enabled", True),
        "schedule": {
            "kind": "cron",
            "cron_expr": sched.get("expr"),
            "timezone": sched.get("tz", "Asia/Shanghai"),
            "timezone_explicit": True,
        },
        "session": {
            "mode": mode,
            "feishu_chat_id": feishu_chat_id,
        },
        "next_run_at": None,
        "last_run_at": None,
        "run_count": 0,
        "last_error": None,
    }


def main():
    apply = "--apply" in sys.argv
    all_tasks = "--all" in sys.argv
    append = "--append" in sys.argv

    if not OPENCLAW_JOBS.exists():
        print(f"[ERROR] 未找到 OpenClaw 任务文件: {OPENCLAW_JOBS}")
        return

    oc_data = json.loads(OPENCLAW_JOBS.read_text(encoding="utf-8"))
    oc_jobs = oc_data.get("jobs", [])

    # 筛选 agentTurn 类型
    agent_jobs = [j for j in oc_jobs if j.get("payload", {}).get("kind") == "agentTurn"]

    # 默认只选启用的
    if not all_tasks:
        agent_jobs = [j for j in agent_jobs if j.get("enabled", True)]

    if not agent_jobs:
        label = "可迁移任务" if all_tasks else "启用的可迁移任务"
        print(f"未找到{label}。")
        return

    converted = [convert_task(j) for j in agent_jobs]

    label = "可迁移任务" if all_tasks else "启用的可迁移任务"
    print(f"找到 {len(converted)} 条{label}：\n")
    for t in converted:
        status = "[启用]" if t["enabled"] else "[禁用]"
        print(f"  {status} {t['name']}")
        print(f"       Cron: {t['schedule']['cron_expr']} ({t['schedule']['timezone']})")
        if t["session"]["feishu_chat_id"]:
            print(f"       飞书: {t['session']['feishu_chat_id']}")
        print(f"       Prompt: {t['prompt'][:80]}...")
        print()

    if not apply:
        print("预览模式。加 --apply 写入 tasks.json。")
        print("包含已禁用任务：加 --all。追加已有任务：加 --append。")
        return

    TASKS_JSON.parent.mkdir(parents=True, exist_ok=True)

    if append and TASKS_JSON.exists():
        existing = json.loads(TASKS_JSON.read_text(encoding="utf-8"))
        tasks = existing if isinstance(existing, list) else []
    else:
        tasks = []

    tasks.extend(converted)
    TASKS_JSON.write_text(
        json.dumps(tasks, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"[OK] 已写入 {TASKS_JSON}（{len(converted)} 条新任务，共 {len(tasks)} 条）")
    print("启动 miniagent 后会自动加载，运行 .schedules list 查看。")


if __name__ == "__main__":
    main()
