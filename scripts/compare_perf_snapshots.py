#!/usr/bin/env python3
"""对比两次 ``scripts/perf_profile_tracemalloc.py --json-out`` 的输出（基线 vs 当前）。

用法:
  python scripts/compare_perf_snapshots.py tests/performance/baselines/local.json perf-snapshot.json
  python scripts/compare_perf_snapshots.py perf-snapshot.json perf-snapshot.json --warn-ratio 1.25

退出码: 0 正常；1 超出 --warn-ratio；2 文件缺失、JSON 无效或根类型非对象。
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def _load(path: str) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    p = argparse.ArgumentParser(description="Compare two perf JSON snapshots")
    p.add_argument("baseline", help="Baseline JSON (e.g. committed perf_baselines/*.json)")
    p.add_argument("current", help="Current run JSON (e.g. CI artifact perf-snapshot.json)")
    p.add_argument(
        "--warn-ratio",
        type=float,
        default=1.35,
        metavar="R",
        help="若 current.peak > baseline.peak * R 则非零退出（仅当两侧均有 tracemalloc_peak_mib）",
    )
    args = p.parse_args()

    try:
        a = _load(args.baseline)
        b = _load(args.current)
    except OSError as e:
        print(f"read error: {e}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
        print(f"json error: {e}", file=sys.stderr)
        return 2

    if not isinstance(a, dict) or not isinstance(b, dict):
        print(
            "error: baseline and current JSON roots must be objects (dict), "
            f"got {type(a).__name__!r} and {type(b).__name__!r}",
            file=sys.stderr,
        )
        return 2

    ra, rb = a.get("inner_repeat"), b.get("inner_repeat")
    if ra is not None and rb is not None and int(ra) != int(rb):
        print(
            f"WARN: inner_repeat differs (baseline={ra!r}, current={rb!r}); "
            "tracemalloc_peak_mib comparison may not be apples-to-apples.",
            file=sys.stderr,
        )

    keys = ("scenario", "inner_repeat", "tracemalloc_peak_mib", "no_tracemalloc")
    print("--- baseline:", args.baseline)
    for k in keys:
        if k in a:
            print(f"  {k}: {a[k]}")
    print("--- current:", args.current)
    for k in keys:
        if k in b:
            print(f"  {k}: {b[k]}")

    pa, pb = a.get("tracemalloc_peak_mib"), b.get("tracemalloc_peak_mib")
    if pa is not None and pb is not None and isinstance(pa, (int, float)) and isinstance(pb, (int, float)):
        ratio = float(pb) / float(pa) if float(pa) > 0 else 0.0
        print(f"--- peak ratio (current/baseline): {ratio:.3f}")
        if float(pb) > float(pa) * float(args.warn_ratio):
            print(
                f"WARN: current peak {pb} MiB > baseline {pa} MiB * {args.warn_ratio}",
                file=sys.stderr,
            )
            return 1
    else:
        print("(skip ratio check: missing numeric tracemalloc_peak_mib on one side)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
