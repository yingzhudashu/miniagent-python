# 第三方技能许可与合规清单

> 本文件是 `workspaces/skills/` 下第三方/模板技能来源与许可说明的 **SSOT**。  
> 运行时技能包默认放在本目录；仓库预置模板位于 `miniagent/assistant/skills/templates/`。

## 目录约定

| 路径 | 用途 |
|------|------|
| `workspaces/skills/` | 用户本机已安装/复制的技能包（默认运行时根，多数内容被 gitignore） |
| `miniagent/assistant/skills/templates/` | 随源码/wheel 发布的模板与基线技能（可复制或 `install_skill` 到上表目录） |

仅从 PyPI 安装 wheel、无完整仓库树时，本目录可能为空；需要基线技能时可由运行时从已打包的 `miniagent/assistant/skills/templates/` 引导安装，或克隆仓库后查看模板源码。

## 模板与基线技能

| 技能 | 模板路径 | 来源 | 许可摘要 |
|------|----------|------|----------|
| `skill-creator` | `miniagent/assistant/skills/templates/skill-creator/` | [anthropics/skills](https://github.com/anthropics/skills)（含 `LICENSE.txt`） | Apache-2.0（见模板内 `LICENSE.txt`） |
| `skill-vetter` | `miniagent/assistant/skills/templates/skill-vetter/` | 本仓库维护的指令型安全审查技能 | 与本仓库相同（MIT）；不含可执行第三方代码 |
| `builtin-web` | `miniagent/assistant/skills/templates/builtin-web/` | 本仓库维护的联网工具技能（Tavily / fetch / Playwright） | 与本仓库相同（MIT）；调用方需自行遵守 Tavily 等第三方 API 条款 |
| `builtin-stackexchange` | `miniagent/assistant/skills/templates/builtin-stackexchange/` | 本仓库维护的 Stack Exchange 排障检索技能 | 与本仓库相同（MIT）；调用方需遵守 Stack Exchange API 条款及署名要求 |

`skill-creator` 首次使用时可复制整个模板目录到 `workspaces/skills/skill-creator/`，或按 [USER_GUIDE.md §7](../../docs/USER_GUIDE.md#7-技能与-clawhub可选) 的安装指引操作。`skill-vetter` 同理，可由 Agent 的 `install_skill` 工具安装或手动复制；项目没有 `miniagent install-skill` 控制台子命令。

## ClawHub / 自行安装的技能

从 [ClawHub](https://clawhub.ai) 或其它来源安装的技能 **不在本清单逐项担保**：

1. 安装前用 `skill-vetter`（或等价人工审查）检查提示词、脚本与网络/文件访问面。
2. 保留上游作者、仓库 URL、版本/commit 与许可证文本；若技能自带 `LICENSE` / `NOTICE`，勿删除。
3. 附加引导安装可用 `scripts/bootstrap_clawhub_skills.py`（参数以官方技能页为准；**不替代**上表基线模板）。

将新的长期第三方技能并入团队仓库时，请在本文件增补一行（名称、来源 URL、许可、备注），并确保 `.gitignore` 对敏感运行时文件的忽略仍有效。

## 相关文档

- [USER_GUIDE.md §7](../../docs/USER_GUIDE.md#7-技能与-clawhub可选) — 日常技能与 ClawHub 使用
- [CONTRIBUTING.md](../../docs/CONTRIBUTING.md) Part 2 — 编写自定义技能
- [SECURITY.md](../../docs/SECURITY.md) — 沙箱与高风险工具确认
