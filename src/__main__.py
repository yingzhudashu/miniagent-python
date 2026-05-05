"""Mini Agent Python — CLI entry point.

Usage:
    python -m src              # Start interactive CLI
    python -m src --feishu     # Start Feishu poll mode
"""

import sys

from src.cli.cli import main

if __name__ == "__main__":
    main()
