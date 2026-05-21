"""Session-scoped NDJSON append for DEBUG_MODE (Cursor). Do not log secrets.

Debug logging is disabled unless MINIAGENT_DEBUG_SESSION_ID is set.
The log file path can be overridden via MINIAGENT_DEBUG_LOG_PATH.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

_SESSION = os.environ.get("MINIAGENT_DEBUG_SESSION_ID", "")
if _SESSION:
    _LOG = Path(
        os.environ.get(
            "MINIAGENT_DEBUG_LOG_PATH",
            str(Path(__file__).resolve().parents[2] / f"debug-{_SESSION}.log"),
        )
    )
else:
    _LOG = None


def agent_debug_log(
    *,
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict | None = None,
    run_id: str = "pre",
) -> None:
    if not _SESSION:
        return
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
