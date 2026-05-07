"""Mini Agent Python — 基于 LLM 的智能个人助手

采用两阶段架构（规划 → 执行），支持：
- 工具调用（文件系统、命令执行、网页搜索）
- 可插拔技能系统
- 自我优化子系统
- 飞书集成
- 多会话隔离

启动方式：
    python -m src          # CLI 交互模式
    python -m src --feishu # 飞书长轮询模式
"""

__version__ = "1.0.0"
