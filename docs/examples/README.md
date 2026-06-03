# 示例与脱敏片段

本目录用于存放**可随仓库提交**的、**不含真实密钥**的配置形状示例或文档化片段。

- **真实运行时状态**（会话、记忆索引、飞书去重等）仍应落在 `MINIAGENT_PATHS_STATE_DIR` 指向的目录（默认常为仓库下 `workspaces/`，且大部分路径已被根目录 `.gitignore` 忽略）。
- **模型与 Agent 配置**请使用 `config.user.json`（见 `config.defaults.json`）；敏感凭据放在 `secrets` 部分。

请勿在本目录提交任何真实 `apiKey`、`sk-` 前缀密钥或飞书 App Secret。

## 目录内容

| 文件 | 说明 |
|------|------|
| `config.example.json` | 配置文件示例，展示常用配置项的结构 |
| `env.example.txt` | 环境变量示例，展示常用环境变量的设置方式 |

## 使用方式

### 配置文件

1. 复制 `config.example.json` 到项目根目录并重命名为 `config.user.json`
2. 替换 `secrets.openai_api_key` 为您的真实 API Key
3. 根据需要调整其他配置项

### 环境变量

1. 复制 `env.example.txt` 中的内容到您的 shell 配置文件（如 `.bashrc`、`.zshrc`）
2. 或创建 `.env` 文件并使用 dotenv 加载
3. 替换所有 `xxx-` 开头的占位符为您的真实值
