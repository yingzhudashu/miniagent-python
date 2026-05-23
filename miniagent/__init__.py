"""Mini Agent Python — 基于 LLM 的智能个人助手

采用两阶段架构（规划 + 执行），支持：
- 工具调用（文件系统、命令执行、网页搜索）
- 可插拔技能系统
- 自我优化子系统
- 飞书集成
- 多会话隔离与多进程实例注册

工程约定（扩展阅读见 ``docs/``）：
- **单一源码包**：可导入包名为 ``miniagent``；``pyproject.toml`` 仅打包 ``miniagent*``。
- **版本号**：本模块 ``__version__`` 为发布权威；``pyproject.toml`` 通过 ``dynamic.version`` 读取。
- **入口**：用户进程请使用 ``python -m miniagent`` 或控制台脚本 ``miniagent``（见 ``project.scripts``）。
- **组合根**：进程级依赖集中在 ``RuntimeContext``（``miniagent.runtime.context``），由 ``compat.unified_entry`` 构造；子系统避免再挂模块级全局单例。
- **状态目录**：默认写入仓库下 ``workspaces/``；可通过环境变量 ``MINI_AGENT_STATE`` 迁出（测试与多实例场景推荐），详见 ``docs/ENGINEERING.md`` 与 ``docs/INSTANCE_REGISTRY.md``。
- **文档索引**：``docs/INDEX.md``；架构总览 ``docs/ARCHITECTURE.md``。

启动方式：
    python -m miniagent          # 仅 CLI（默认）
    python -m miniagent --feishu # CLI + 飞书同时启动（飞书侧为 WebSocket 长轮询）
"""

__version__ = "2.0.2"
__author__ = "mini-agent"
__description__ = "基于 LLM 的智能个人助手，采用两阶段架构（规划 + 执行）"

__all__ = ["__version__", "__author__", "__description__"]
