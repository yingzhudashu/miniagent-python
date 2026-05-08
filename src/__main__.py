"""Mini Agent Python — 统一入口

启动 Agent 后默认进入 CLI 模式。
运行时可通过 `.feishu start` 动态启用飞书连接。

用法:
    python -m src              # CLI 模式（默认）
    python -m src --feishu     # CLI + 飞书同时启动
    python -m src --force      # 强制获取实例锁
    python -m src --stop       # 停止运行中的实例

架构:
- 一套 registry / monitor / skill_registry / session_manager
- CLI 通过 stdin 交互，飞书通过 WebSocket 长轮询（运行时可插拔）
- 共享 UnifiedEngine 管理思考回调和会话路由
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
        pass


def main():
    """统一入口 — 始终走 unified 路径。"""
    _load_env()

    if "--stop" in sys.argv:
        try:
            from src.core.instance_manager import stop_instance
            result = stop_instance()
            if result.get("success"):
                print("✅ Mini Agent 已停止")
            else:
                print(f"ℹ️ {result.get('reason', '未运行')}")
        except Exception as e:
            print(f"❌ 停止失败: {e}")
        sys.exit(0)

    from src.unified import unified_entry
    unified_entry()


if __name__ == "__main__":
    main()
