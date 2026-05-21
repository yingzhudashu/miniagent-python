# 示例与脱敏片段

本目录用于存放**可随仓库提交**的、**不含真实密钥**的配置形状示例或文档化片段。

- **真实运行时状态**（会话、记忆索引、飞书去重等）仍应落在 `MINI_AGENT_STATE` 指向的目录（默认常为仓库下 `workspaces/`，且大部分路径已被根目录 `.gitignore` 忽略）。
- **模型与 Agent 配置**请使用项目根 `.env`（见 [.env.example](../../.env.example)）；曾支持的 OpenClaw JSON（`MINIAGENT_CONFIG`）已移除。

请勿在本目录提交任何真实 `apiKey`、`sk-` 前缀密钥或飞书 App Secret。
