# LLM 提供商与模型角色

> Mini Agent Python | 版本: 4.0.0 | 最后更新: 2026-07-17

MiniAgent 3.0 将“提供商”“wire API”“模型 profile”和“助手阶段角色”分开管理。
默认回答不会按价格或厂商自动换模型；只有 `llm.roles` 中的显式绑定可以跨模型或提供商。

## 支持范围

| driver | wire API | 安装 |
|---|---|---|
| `openai` | `openai_chat`、`openai_responses` | 核心依赖 |
| `anthropic` | `anthropic_messages` | `pip install "miniagent-python[providers]"` |
| `google` | `google_generate_content` | `pip install "miniagent-python[providers]"` |

DeepSeek、OpenRouter、Qwen、Ollama、vLLM、LM Studio 等 OpenAI 兼容服务使用
`driver: openai` 并设置各自的 `base_url`。兼容差异应写在 model profile 的
`compatibility` 对象中，不能通过业务代码猜测 URL。

## 配置结构

```json
{
  "secrets": {
    "llm": {
      "openai": {"api_key": "sk-..."},
      "anthropic": {"api_key": "..."},
      "google": {"api_key": "..."}
    }
  },
  "llm": {
    "providers": {
      "openai": {
        "driver": "openai",
        "base_url": "https://api.openai.com/v1",
        "credential": "openai",
        "api_key_env": "OPENAI_API_KEY"
      }
    },
    "models": {
      "primary": {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "api": "openai_responses",
        "context_window": 128000,
        "max_output_tokens": 16384,
        "capabilities": {
          "tools": true,
          "vision": true,
          "reasoning": false,
          "structured_output": true
        }
      }
    },
    "roles": {
      "default": "primary",
      "reasoning": "primary",
      "fast": "primary",
      "vision": "primary"
    }
  }
}
```

凭据解析顺序为 `secrets.llm`、provider 的 `api_key_env`、driver 默认环境变量。
认证失败不会静默切换到另一套凭据。日志、诊断和错误事件不得包含密钥或响应正文。

## 角色语义

- `default`：最终回答、工具循环与一般问答。
- `reasoning`：需求澄清、规划、反思、答案复核。
- `fast`：任务分类等短控制请求。
- `vision`：图片描述与视觉输入；绑定模型必须声明 `vision: true`。

未配置的非默认角色回退到 `default`。活动回合启动后持有不可变 gateway/model 快照；
配置热更新或 TUI 模型切换只影响下一回合。

## 模型目录与命令

- `/model`：查看当前模型；TUI 中 `Ctrl+P` 打开可搜索的模型选择浮层。
- `/model <profile>`：切换 `default` 角色。
- 动态发现由 `LLMGateway.refresh()` 或后续 `/model refresh` 调用触发；启动时不联网。
- 动态目录原子写入状态目录的 `llm-model-catalog.json`。刷新失败保留上次成功缓存。
- 用户 profile 覆盖动态缓存，动态缓存覆盖小型内置目录。
- 只有存在可信价格元数据时才计算成本；未知价格显示为未知，而不是 `$0`。

## 从旧版本升级

3.0 运行时不包含迁移器。provider/profile/role、嵌套凭据与状态 schema 的人工迁移步骤
统一见 [MIGRATION.md](MIGRATION.md)。

## 错误与可选依赖

provider 错误统一归类为认证、限流、超时、上下文溢出、模型不存在、不支持参数、
服务不可用、取消或未知错误。已产生文本或工具调用后不得自动重放请求，避免重复回答或
重复执行工具。未安装 Anthropic/Google SDK 时只禁用对应 driver，并给出 providers extra
的安装命令；OpenAI 和其它已配置 provider 不受影响。
