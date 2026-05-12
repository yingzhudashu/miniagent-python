"""Session-scoped NDJSON append for DEBUG_MODE (Cursor). Do not log secrets."""

from __future__ import annotations

import json
import time
from pathlib import Path

_LOG = Path(__file__).resolve().parents[2] / "debug-93e44f.log"
_SESSION = "93e44f"


def agent_debug_log(
    *,
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict | None = None,
    run_id: str = "pre",
) -> None:
    try:
        line = json.dumps(
            {
                "sessionId": _SESSION,
                "runId": run_id,
                "hypothesisId": hypothesis_id,
                "location": location,
                "message": message,
                "data": data or {},
                "timestamp": int(time.time() * 1000),
            },
            ensure_ascii=False,
        )
        with _LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


__all__ = ["agent_debug_log"]
