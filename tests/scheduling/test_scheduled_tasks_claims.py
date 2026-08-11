"""Transactional scheduled-task claim contracts."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from miniagent.assistant.scheduled_tasks.models import ScheduledTask, ScheduleSpec
from miniagent.assistant.scheduled_tasks.store import claim_due_tasks, save_tasks


def test_two_connections_have_one_claim_winner(state_dir: str) -> None:
    save_tasks(
        [
            ScheduledTask(
                id="only-once",
                name="only-once",
                prompt="run",
                schedule=ScheduleSpec(kind="interval", interval_seconds=60),
                next_run_at=time.time() - 1,
            )
        ]
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda owner: claim_due_tasks(owner, lease_seconds=30),
                ("worker-a", "worker-b"),
            )
        )
    assert sorted(len(result) for result in results) == [0, 1]
