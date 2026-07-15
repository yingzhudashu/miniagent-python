"""Mini Agent Python — 基于 LLM 的智能个人助手

采用多阶段架构（Phase 0 分类 → Phase 0.5 需求澄清 → Phase 1 规划 → Phase 2 执行），支持：
- 工具调用（文件系统、命令执行、网页搜索）
- 可插拔技能系统
- 自我优化子系统
- 飞书集成
- 多会话隔离与多进程实例注册

工程约定（扩展阅读见 ``docs/``）：
- **单一源码包**：可导入包名为 ``miniagent``；``pyproject.toml`` 仅打包 ``miniagent*``。
- **版本号**：本模块 ``__version__`` 为发布权威；``pyproject.toml`` 通过 ``dynamic.version`` 读取。
- **入口**：用户进程请使用 ``python -m miniagent`` 或控制台脚本 ``miniagent``（见 ``project.scripts``）。
- **组合根**：进程级依赖集中在唯一 ``ApplicationContainer``，由 ``bootstrap.entrypoint`` 构造并显式传递。
- **状态目录**：默认写入仓库下 ``workspaces/``；可在 ``config.user.json`` 设置 ``paths.state_dir`` 迁出（测试与多实例场景推荐），详见 ``docs/ENGINEERING.md`` §3.3。
- **文档索引**：``docs/INDEX.md``；架构总览 ``docs/ARCHITECTURE.md``。

启动方式：
    python -m miniagent              # 仅 CLI（默认）
    python -m miniagent --continue   # 继续上次 CLI 会话
    python -m miniagent --session <ID>  # 启动并绑定到指定会话
    python -m miniagent --feishu     # CLI + 飞书同时启动（飞书侧为 WebSocket 长轮询）
    python -m miniagent --feishu --continue  # CLI + 飞书，并继续上次会话
"""

__version__ = "3.0.0"
__author__ = "mini-agent"
__description__ = "基于 LLM 的智能个人助手，采用多阶段架构（分类→澄清→规划→执行）"

__all__ = ["__version__", "__author__", "__description__"]
