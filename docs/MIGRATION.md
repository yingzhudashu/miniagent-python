# 迁移到 MiniAgent 3.0

> Mini Agent Python | 版本: 3.0.0 | 最后更新: 2026-07-16 | 3.0 只支持当前配置与状态格式

MiniAgent 3.0 不会自动转换、备份或改写旧配置和状态文件。迁移前先停止旧实例，完整复制
`config.user.json` 与旧状态目录，再让 3.0 使用一个新的空状态目录；不要直接在唯一副本上试跑。

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

1. 使用新状态目录启动 3.0，让当前写入方生成合法空文件。
2. 只复制仍需保留的用户数据，并按新文件字段逐项转换；不要复制旧版本字段或锁/PID。
3. 运行 `/doctor`、`/session list`、`/schedule list` 和相关功能的只读检查。
4. 验证成功后再归档旧目录。MiniAgent 不会替用户删除旧 `.bak`、会话或 workspace。

## 嵌入式 Python API

仅以下包入口属于公开 API：

- `miniagent.llm`
- `miniagent.agent`
- `miniagent.ui`
- `miniagent.assistant`

模型调用通过 `LLMGateway` 并显式指定 `role` 与可选 `profile`。核心 Agent 使用
`AgentServices`、不可变 `AgentSettings` 和必要端口构造。完整产品通过
`run_assistant(argv)` 启动，或由 `create_assistant_application()` 创建后调用
`AssistantApplication.run()`。3.0 不提供 2.x 内部模块路径的兼容导入。
