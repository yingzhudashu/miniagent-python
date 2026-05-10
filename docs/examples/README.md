# 示例与脱敏片段

本目录用于存放**可随仓库提交**的、**不含真实密钥**的配置形状示例或文档化 JSON 片段。

- **真实运行时状态**（会话、记忆索引、飞书去重等）仍应落在 `MINI_AGENT_STATE` 指向的目录（默认常为仓库下 `workspaces/`，且大部分路径已被根目录 `.gitignore` 忽略）。
- 若需演示 OpenClaw 兼容 JSON 的键名结构，见同目录下的 `sample-external-config.fragment.json`；将密钥写入 `apiKey` 的风险与缓解见 [../SECURITY.md](../SECURITY.md) §「外部 JSON（MINIAGENT_CONFIG）与进程环境」。

请勿在本目录提交任何真实 `apiKey`、`sk-` 前缀密钥或飞书 App Secret。
