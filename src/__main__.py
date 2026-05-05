"""Mini Agent Python — 包入口 (Phase 7)

支持:
- `python -m src` 启动 CLI
- `python -m src --feishu` 启动飞书长轮询
- `python -m src --force` 强制获取实例锁
- `python -m src --stop` 停止运行中的实例
"""

from __future__ import annotations

import asyncio
import sys


def main():
    """CLI 入口函数"""
    # 延迟导入，避免未安装依赖时报错
    try:
        from src.cli.cli import main as cli_main
    except ImportError:
        print("❌ 无法导入 CLI 模块，请确保已安装依赖")
        sys.exit(1)

    asyncio.run(cli_main())


if __name__ == "__main__":
    main()
