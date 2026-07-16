"""Thin module entry point for the personal-assistant product."""

from miniagent.assistant import run_assistant


def main() -> None:
    """启动命令行助手应用。"""
    run_assistant()


if __name__ == "__main__":
    main()
