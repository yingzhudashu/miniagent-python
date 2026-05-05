"""Mini Agent Python — CLI entry point.

Interactive command-line interface for the Mini Agent.

Usage:
    python -m src                  # Start interactive CLI
    python -m src --feishu         # Start Feishu poll mode
    python -m src --help           # Show help
"""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    """CLI 主入口"""
    parser = argparse.ArgumentParser(
        description="Mini Agent Python — A minimal LLM agent"
    )
    parser.add_argument(
        "--feishu",
        action="store_true",
        help="Start in Feishu poll mode",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="miniagent-python 1.0.0",
    )

    args = parser.parse_args()

    if args.feishu:
        print("🚀 Starting Feishu poll mode... (not yet implemented)")
        sys.exit(1)

    print("🦞 Mini Agent Python — Interactive CLI (not yet implemented)")
    print("💡 Phase 1 complete: project skeleton + type layer")
    print("📋 Next: Phase 2 — Infrastructure layer")


if __name__ == "__main__":
    main()
