#!/usr/bin/env python3
"""可重复本地剖析：关键词索引批处理 + 可选 tracemalloc。

用法见 docs/PERFORMANCE.md §3。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import tracemalloc
from datetime import datetime, timezone

# 保证仓库根在 path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _run_keyword_index_batch(tmp: str) -> None:
    import asyncio

    from miniagent.memory.keyword_index import KeywordIndex
    from miniagent.memory.store import DefaultMemoryStore
    from miniagent.types.memory import MemoryEntryInput, SessionMemory

    async def _body() -> None:
        ki = KeywordIndex(state_dir=tmp)
        store = DefaultMemoryStore(state_dir=tmp, keyword_index=ki)
        sid = "profile-session"
        now = datetime.now(timezone.utc).isoformat()
        mem = SessionMemory(
            session_id=sid,
            cumulative_summary="",
            key_facts=[],
            entries=[],
            total_turns=0,
            first_seen=now,
            last_active=now,
        )
        await store.save(mem)
        for i in range(20):
            await store.add_entry(
                sid,
                MemoryEntryInput(
                    timestamp=now,
                    user_snippet=f"用户输入片段{i} 投资 偏好",
                    summary=f"摘要{i}",
                    facts=[f"事实{i}"],
                ),
            )
        store.flush_keyword_index()

    asyncio.run(_body())


def _run_keyword_index_batch_repeated(tmp: str, repeat: int) -> None:
    """重复跑热路径，便于 cProfile 下减少冷启动占比（见 docs/PERFORMANCE.md §3）。

    每次迭代使用独立子目录，避免在同一 state_dir 上无限累积条目。
    """
    n = max(1, repeat)
    for i in range(n):
        sub = os.path.join(tmp, f"run_{i}")
        os.makedirs(sub, exist_ok=True)
        _run_keyword_index_batch(sub)


def main() -> int:
    p = argparse.ArgumentParser(description="Miniagent perf profiling helper")
    p.add_argument("--no-tracemalloc", action="store_true", help="仅跑热路径，不启用 tracemalloc")
    p.add_argument("--top", type=int, default=15, help="tracemalloc 打印条数")
    p.add_argument("--json-out", type=str, default="", help="写入摘要 JSON 路径（供 CI artifact）")
    p.add_argument(
        "--inner-repeat",
        type=int,
        default=1,
        metavar="N",
        help="在父临时目录下创建 run_0..run_{N-1} 子目录，各跑一批 keyword_index+store（cProfile 时建议 >=50 以突出热路径）",
    )
    args = p.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        if args.no_tracemalloc:
            _run_keyword_index_batch_repeated(tmp, args.inner_repeat)
            peak_mb = None
        else:
            tracemalloc.start()
            try:
                tracemalloc.reset_peak()
                _run_keyword_index_batch_repeated(tmp, args.inner_repeat)
                _cur, peak = tracemalloc.get_traced_memory()
                peak_mb = round(peak / (1024 * 1024), 4)
                snap = tracemalloc.take_snapshot()
                stats = snap.statistics("lineno")[: args.top]
                print(f"tracemalloc peak ~{peak_mb} MiB (traced allocation peak)\n")
                for s in stats:
                    print(s)
            finally:
                tracemalloc.stop()

    payload = {
        "scenario": "keyword_index_batch_20",
        "inner_repeat": int(args.inner_repeat),
        "tracemalloc_peak_mib": peak_mb,
        "no_tracemalloc": bool(args.no_tracemalloc),
    }
    if args.json_out:
        os.makedirs(os.path.dirname(os.path.abspath(args.json_out)), exist_ok=True)
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        print(f"Wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
