# Mini Agent Python — 文档索引

> 用户入门以 **[README.md](../README.md)** 为准。  
> Mini Agent Python | 版本: 3.0.0 | 最后更新: 2026-07-16 | 与 `miniagent.__version__` 对齐 | 未发版行为以 [CHANGELOG](../CHANGELOG.md) `[Unreleased]` 为准

---

## SSOT 速查（单一事实来源）

| 主题 | 权威文档 |
|------|----------|
| 用户安装与配置 | [README.md](../README.md) §安装、§配置 |
| 从 2.x 人工迁移到 3.0 | [MIGRATION.md](MIGRATION.md) |
| LLM provider、模型与角色 | [LLM_PROVIDERS.md](LLM_PROVIDERS.md) |
| 架构概览 | [README.md](../README.md) §架构概览；深读 [ARCHITECTURE.md](ARCHITECTURE.md) |
| 通道绑定 | [FEISHU.md](FEISHU.md) §通道绑定 |
| 多实例 / `--stop` | [ENGINEERING.md](ENGINEERING.md) §3.3 |
| Trace 实现 | [ENGINEERING.md](ENGINEERING.md) §5 |
| 自我优化操作 | [SELF_OPT.md](SELF_OPT.md) |
| 输出格式 | [OUTPUT_FORMAT.md](OUTPUT_FORMAT.md) |
| 提示词规范 | [PROMPT_GUIDELINES.md](PROMPT_GUIDELINES.md) |
| 环境变量分类 | [ENGINEERING.md](ENGINEERING.md) §1.2 |
| 知识库 / RAG | [KNOWLEDGE_BASE.md](KNOWLEDGE_BASE.md) |
| 安全模型 | [SECURITY.md](SECURITY.md) |
| 完整 SSOT 表 | [ENGINEERING.md](ENGINEERING.md) §1（含知识库等扩展行） |

**卫星文档**（MEMORY、SECURITY、PERFORMANCE、CONTRIBUTING 等）：在各自文件写全细节；INDEX 仅分类导航，不重复 SSOT 表。

---

## 测试与质量

- **测试数量**：以 `pytest tests/ --collect-only -q` 收集结果为准
- **覆盖率**：CI 使用分支模式并以 80% 作为整体门禁，同时要求本次修改行覆盖率 ≥95%；当前实测值和命令见 [ENGINEERING.md](ENGINEERING.md) §2
- **测试矩阵**：测试文件和 CI workflow 是可执行事实来源，不再维护人工状态表

```bash
pytest tests/ -q -m "not evaluation"
pytest tests/ --cov=miniagent --cov-report=html
```

---

## 文档分类

### 核心文档
[README](../README.md) · [USER_GUIDE.md](USER_GUIDE.md) · [MIGRATION.md](MIGRATION.md) · [CHANGELOG](../CHANGELOG.md)

### 用户与运维
[CLI.md](CLI.md) · [LLM_PROVIDERS.md](LLM_PROVIDERS.md) · [DEPLOYMENT.md](DEPLOYMENT.md) · [TROUBLESHOOTING.md](TROUBLESHOOTING.md) · [FEISHU.md](FEISHU.md)（含通道绑定） · [KNOWLEDGE_BASE.md](KNOWLEDGE_BASE.md)

### 架构与专题
[ARCHITECTURE.md](ARCHITECTURE.md) · [MEMORY_SYSTEM.md](MEMORY_SYSTEM.md) · [SECURITY.md](SECURITY.md) · [SELF_OPT.md](SELF_OPT.md) · [OUTPUT_FORMAT.md](OUTPUT_FORMAT.md)

### 性能
[PERFORMANCE.md](PERFORMANCE.md) — Part A 度量与测试 · Part B 运行时调优

### 开发者路径
[CONTRIBUTING.md](CONTRIBUTING.md)（Part 1 贡献 · Part 2 扩展 · Part 3 API）→ [PROMPT_GUIDELINES.md](PROMPT_GUIDELINES.md)

### 维护者
[ENGINEERING.md](ENGINEERING.md)（§3.3 多实例注册表、§2 质量门禁）

---

## 外部链接

- **ClawHub 技能市场**: https://clawhub.ai
- **OpenClaw 文档**: https://docs.openclaw.ai
- **OpenClaw 社区**: https://discord.com/invite/clawd
