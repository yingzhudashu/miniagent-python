"""Console stream setup shared by every process entry point."""

from __future__ import annotations

import sys


def configure_console_encoding() -> None:
    """Write Unicode deterministically when Windows output is piped or captured.

    Windows selects a legacy code page for some non-interactive streams.  The
    application emits Chinese status text, so every entry point configures both
    streams before its first user-visible write.  Other platforms retain their
    interpreter-selected encoding.
    """
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


__all__ = ["configure_console_encoding"]
