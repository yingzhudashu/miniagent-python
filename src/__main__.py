"""Mini Agent Python — 包入口

支持:
- `python -m src` 启动 CLI
- `python -m src --feishu` 启动飞书长轮询
- `python -m src --unified` CLI + 飞书同时运行（共享子系统）
- `python -m src --unified --feishu` 同上（显式指定）
- `python -m src --force` 强制获取实例锁
- `python -m src --stop` 停止运行中的实例
"""

from __future__ import annotations

import asyncio
import os
import sys


def _load_env():
    """加载项目根目录 .env 文件"""
    try:
        from dotenv import load_dotenv
        env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
        if os.path.exists(env_path):
            load_dotenv(env_path)
    except ImportError:
        pass  # python-dotenv 未安装，跳过


def main():
    """入口函数"""
    _load_env()

    # 统一模式：CLI + 飞书同时运行
    if "--unified" in sys.argv:
        try:
            from src.unified import unified_entry
            unified_entry()
            return
        except ImportError as e:
            print(f"❌ 无法导入统一模块: {e}")
            sys.exit(1)

    # 纯飞书模式
    if "--feishu" in sys.argv and "--unified" not in sys.argv:
        try:
            from src.feishu.poll_server import feishu_main
            asyncio.run(feishu_main())
            return
        except ImportError:
            print("❌ 无法导入飞书模块，请确保已安装依赖")
            sys.exit(1)

    # 默认：CLI 模式
    try:
        from src.cli.cli import main as cli_main
    except ImportError:
        print("❌ 无法导入 CLI 模块，请确保已安装依赖")
        sys.exit(1)

    asyncio.run(cli_main())


if __name__ == "__main__":
    main()
