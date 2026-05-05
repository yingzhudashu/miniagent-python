# Mini Agent Python

> 从 TypeScript 迁移而来的 Python 版 Mini Agent

## 简介

Mini Agent 是一个基于 LLM 的最小化智能代理，采用**两阶段架构（Plan-then-Execute）**和**技能系统（Skill System）**。

### 核心特性

- **两阶段架构**: Phase 1 规划 → Phase 2 ReAct 执行
- **技能系统**: 自动发现、加载、合并技能贡献
- **自我优化**: 代码质量检查、自动修复、反馈闭环
- **多会话管理**: 会话隔离、工作空间、升降维
- **三层记忆**: 上下文记忆 + 会话记忆 + 语义检索
- **飞书集成**: WebSocket 长轮询 + Webhook 双模式

## 环境要求

- Python 3.10+
- OpenAI 兼容 API（OpenAI / 本地部署）

## 快速开始

```bash
# 1. 安装依赖
pip install -e ".[dev]"

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，填入 API Key

# 3. 启动 CLI
python -m src

# 4. 运行测试
pytest
```

## 项目结构

```
src/
├── types/          # 类型定义（dataclass + Protocol）
├── core/           # 核心引擎
│   ├── agent.py    # 薄编排层
│   ├── planner.py  # Phase 1: LLM 规划
│   ├── executor.py # Phase 2: ReAct 执行
│   ├── registry.py # 工具注册表
│   ├── config.py   # 双层配置体系
│   └── self_opt/   # 自我优化子系统
├── tools/          # 工具实现
├── session/        # 会话管理
├── feishu/         # 飞书适配层
├── security/       # 沙盒安全
├── cli/            # CLI 入口
└── skills/         # 技能系统
```

## 架构

详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## 迁移说明

本项目从 [mini-agent](https://github.com/yingzhudashu/mini-agent)（TypeScript）迁移而来，保持接口等价，使用 Python 惯用写法。

## License

MIT
