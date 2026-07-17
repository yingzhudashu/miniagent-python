# 迁移到 MiniAgent 4.0

> Mini Agent Python | 版本: 4.0.0 | 最后更新: 2026-07-17 | 4.0 保持 `llm.*` 配置与 3.0 状态数据兼容

MiniAgent 4.0 保留 3.0 的 `llm.*` 配置、会话、Memory、知识库和 Trace 文件格式，不执行
`llm → ai` 改名。迁移前仍应停止旧实例并备份 `config.user.json` 与状态目录。2.x 用户须先完成
下述 provider/model/role 和状态 schema 转换；3.0 用户可以直接使用原状态目录。

## 配置迁移

把旧的单模型配置改为 provider、model profile 和 role 三层结构：

```json
{
  "llm": {
    "providers": {
      "openai": {
        "driver": "openai",
        "credential": "openai",
        "api_key_env": "OPENAI_API_KEY"
      }
    },
    "models": {
      "primary": {
        "provider": "openai",
        "model": "gpt-5-mini",
        "api": "openai_responses",
        "defaults": {"temperature": 0.7, "max_tokens": 4096}
      }
    },
    "roles": {
      "default": "primary",
      "fast": "primary",
      "reasoning": "primary",
      "vision": "primary"
    }
  },
  "secrets": {
    "llm": {"openai": {"api_key": "..."}}
  }
}
```

密钥也可以只保存在 provider 的环境变量中。厂商兼容差异写入所选 model profile 的
`compatibility` 对象；Qwen thinking 需显式设置 `"thinking_adapter": "qwen"`。完整字段见
[LLM_PROVIDERS.md](LLM_PROVIDERS.md) 和包内 `config.defaults.json`。

## 状态文件迁移

每个 JSON 状态文件都必须是对象，并带有该写入方当前精确的 `schema_version`。使用旧
`version` 字段、缺少版本、版本不一致或数组根的文件都会在不修改原文的情况下被拒绝。

当前内置 schema：

| 状态 | schema_version |
|------|----------------|
| 会话配置、路由、知识库、Dream、长期记忆、自优化、测试报告、实例元数据 | `1` |
| 会话历史、定时任务 | `2` |

推荐流程：

1. 从 2.x 升级时使用新状态目录启动 4.0，让当前写入方生成合法空文件。
2. 只复制仍需保留的用户数据，并按新文件字段逐项转换；不要复制旧版本字段或锁/PID。
3. 运行 `/doctor`、`/session list`、`/schedule list` 和相关功能的只读检查。
4. 验证成功后再归档旧目录。MiniAgent 不会替用户删除旧 `.bak`、会话或 workspace。

## 嵌入式 Python API

仅以下包入口属于公开 API：

- `miniagent.llm`
- `miniagent.agent`
- `miniagent.ui`
- `miniagent.assistant`

模型调用继续通过 `LLMGateway`，Embedding 通过 `EmbeddingClient`。可复用 Agent 使用
`AgentRuntime(AgentSpec, llm, extensions)`，通过统一 `AgentEvent` 输出状态。UI 实现
`UISurface`；实例由 `AssistantSpec` 和 `create_assistant()` 构造。默认产品仍可通过
`run_assistant(argv)` 或 `create_personal_assistant()` 启动。4.0 不恢复 2.x 内部导入路径。
