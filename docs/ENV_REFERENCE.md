# 环境变量参考

本文档列出所有影响 miniagent-python 运行时行为的环境变量。

> 提示：复制 `.env.example` 为 `.env` 后填写；勿提交真实密钥。

---

## 1. API 凭据（必填）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OPENAI_API_KEY` | *(空)* | LLM API 密钥，启动必须 |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | API 基础 URL，可指向兼容 OpenAI 的第三方服务 |
| `OPENAI_MODEL` | `gpt-4o-mini` | 主模型名称 |
| `TAVILY_API_KEY` | *(空)* | Web 搜索 API 密钥（`web_search` 技能工具需要） |
| `FEISHU_APP_ID` | *(空)* | 飞书应用 ID（`--feishu` 启动时必须） |
| `FEISHU_APP_SECRET` | *(空)* | 飞书应用 Secret |
| `FEISHU_VERIFICATION_TOKEN` | *(空)* | 飞书事件订阅验证 Token |
| `FEISHU_ENCRYPT_KEY` | *(空)* | 飞书事件加密 Key（可选） |

## 2. 模型行为

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `AGENT_CONTEXT_WINDOW` | `128000` | 上下文窗口大小（token 数） |
| `OPENAI_MAX_TOKENS` | *(空)* | 最大输出 token 数 |
| `AGENT_THINKING_DEFAULT` | `medium` | 思考档位 |
| `OPENAI_THINKING_BUDGET` | *(空)* | 思考预算 token（若与 AGENT_THINKING_DEFAULT 同设，以此为准） |
| `MINIAGENT_ANNOUNCE_DIFFICULTY_AND_PLAN` | `1` | 是否向用户展示任务难度与规划摘要 |

## 3. Agent 执行

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `AGENT_MAX_TURNS` | `400` | Agent 最大对话轮次 |
| `MINIAGENT_STEP_MAX_TURNS` | `48` | 分步执行时每步 ReAct 子循环上限 |
| `MINIAGENT_THINKING_SEGMENT_SEPARATOR` | *(双换行)* | 同一步内多轮思考拼接符 |
| `MINIAGENT_TOOL_INTENT_MAX_CHARS` | `4000` | 工具执行前意图行最大字符数 |
| `MINIAGENT_TOOL_INTENT_IN_THINKING` | `0` | 是否在工具执行前推送意图行 |
| `MINIAGENT_TOOL_FINISH_VERBOSE` | `0` | `1` 时 `on_tool_finish` 落盘含参数与输出 |
| `AGENT_TOOL_TIMEOUT` | `60` | 单个工具超时秒数 |
| `AGENT_HTTP_TIMEOUT` | `120` | HTTP 请求超时秒数 |
| `AGENT_CONTEXT_RESERVE` | `0.15` | 上下文预留比例 |
| `AGENT_CONTEXT_COMPRESS_THRESHOLD` | `0.6` | 上下文压缩阈值 |
| `AGENT_DEBUG` | `false` | 调试模式 |
| `AGENT_LOG_TOKEN_USAGE` | `true` | 是否记录 token 使用量 |
| `MINIAGENT_TASK_CLASSIFIER` | `1` | 是否启用规划前任务难度分类 |
| `MINIAGENT_PHASED_EXECUTION` | `1` | 是否启用分步执行模式 |
| `MINIAGENT_REQUIREMENT_CLARIFY` | `1` | 是否启用规划前需求澄清（`0` 关闭） |
| `MINIAGENT_REFLECTION` | `1` | 是否启用执行后自我反思（`0` 关闭） |
| `MINIAGENT_THINKING_MERGE_TOOLS` | `1` | 合并相邻工具执行的 thinking 记录 |
| `MINIAGENT_MEMORY_STORE_CACHE_MAX` | `50` | 记忆存储最大缓存条目数 |
| `MINIAGENT_KEYWORD_INDEX_MAX` | `20000` | 关键词索引最大条目数 |
| `LOOP_DETECTION_ENABLED` | `true` | 是否启用循环检测 |
| `LOOP_HISTORY_SIZE` | `50` | 循环检测历史窗口大小 |
| `LOOP_WARNING_THRESHOLD` | `8` | 循环检测警告阈值 |
| `LOOP_CRITICAL_THRESHOLD` | `12` | 循环检测临界阈值 |

## 4. 飞书核心体验

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MINIAGENT_FEISHU_REPLY_PLAIN` | `0` | `0`=完整 Markdown；`1`=弱化标记（仍为 `lark_md`） |
| `MINIAGENT_FEISHU_REPLY_TARGET` | `reply` | `reply`=回复入站消息；`create`=新建消息 |
| `MINIAGENT_FEISHU_CARD_ACTION_ROUTER` | `1` | `1`=注册卡片按钮回调 |
| `MINIAGENT_FEISHU_TOOLS_AUTO` | `1` | 未设 TOOLS 时，`1` 且已配凭据则自动注册 |
| `MINIAGENT_FEISHU_TOOLS` | *(空)* | 显式控制：`1`=启用，`0`=禁用（优先级高于 TOOLS_AUTO） |
| `MINIAGENT_FEISHU_DOT_COMMANDS_FULL` | `0` | `1`=飞书点命令与 CLI 同等功能 |
| `MINIAGENT_FEISHU_USER_ACCESS_TOKEN` | *(空)* | OAuth token；`feishu_doc action=search` 需要 |
| `MINIAGENT_FEISHU_CARD_EXTRACT_INBOUND` | `1` | `1`=入站 interactive 消息抽取可读文本 |
| `MINIAGENT_FEISHU_MARKDOWN_COMMANDS` | `0` | `1`=飞书 `.session` 等命令输出 Markdown 表格 |
| `MINIAGENT_FEISHU_REPLY_IN_THREAD` | `0` | 与 `REPLY_TARGET=reply` 联用，话题内回复 |

## 5. 飞书文档/媒体/网络

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `FEISHU_DOC_FOLDER_FALLBACK_ROOT_META` | `1` | 无 folder_token 时尝试根目录元数据 API |
| `MINIAGENT_FEISHU_DOCX_URL_PREFIX` | *(空)* | 文档 URL 前缀 |
| `MINIAGENT_FEISHU_DOC_FOLDER_TOKEN` | *(空)* | 文档目录 token |
| `MINIAGENT_FEISHU_MEDIA_RUN_AGENT` | `0` | `1`=收到文件/图片后触发 Agent 处理 |
| `MINIAGENT_FEISHU_MEDIA_VISION_DESC` | `1` | `1`=收到图片时调用视觉模型生成描述 |
| `MINIAGENT_FEISHU_MEDIA_SILENT_REPLY` | `0` | 媒体消息静默回复 |
| `MINIAGENT_FEISHU_RECEIVE_ID_TYPE` | `chat_id` | 接收消息 ID 类型 |

## 6. 飞书卡片渲染调优

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MINI_AGENT_THINKING_FOR_LLM_MAX_CHARS` | `10000` | 飞书卡片中 thinking 正文最大字符 |
| `MINI_AGENT_FEISHU_CARD_BODY_MAX` | `48000` | 飞书卡片 body 最大字符 |

## 7. 飞书 WebSocket 长连接

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MINIAGENT_FEISHU_WS_AUTO_RECONNECT` | `0` | 自动重连 |
| `MINIAGENT_FEISHU_WS_WATCHDOG_INTERVAL_S` | `30` | 看门狗检测间隔（秒） |
| `MINIAGENT_FEISHU_WS_DEAD_CONN_GRACE_S` | `90` | 死连接宽限期（秒） |
| `MINIAGENT_FEISHU_WS_RECONNECT_GRACE_S` | `300` | 重连宽限期（秒） |
| `MINIAGENT_FEISHU_WS_REFRESH_INTERVAL_S` | *(跟随 SDK 默认)* | WebSocket 连接刷新间隔（秒） |
| `MINIAGENT_FEISHU_WS_IDLE_REFRESH_S` | *(跟随 SDK 默认)* | 空闲时 WebSocket 刷新间隔（秒） |

## 8. 定时任务 & 时区

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MINIAGENT_DISABLE_SCHEDULED_TASKS` | `0` | `1` 时禁用定时任务 |
| `MINIAGENT_SCHEDULE_DISPATCH_BACKOFF` | `60` | 任务调度退避秒数 |
| `MINIAGENT_SCHEDULE_TIMEZONE` | *(跟随 TZ / MINIAGENT_TIMEZONE)* | 仅覆盖定时任务默认时区 |
| `MINIAGENT_SCHEDULE_FEISHU_MIRROR` | `1` | 定时任务飞书镜像 |
| `MINIAGENT_SCHEDULE_FEISHU_LAST_CHAT` | `0` | 定时任务最近聊天 |
| `MINIAGENT_SCHEDULE_TOOLS` | `1` | 定时任务工具注册 |
| `TZ` | *(系统时区)* | 标准时区环境变量，如 `Asia/Shanghai` |
| `MINIAGENT_TIMEZONE` | *(跟随 TZ)* | 与 TZ 二选一，优先级更高 |

## 9. Web 搜索 & 浏览器

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `WEB_SEARCH_API_KEY` | *(空)* | 与 `TAVILY_API_KEY` 任一即可 |
| `TAVILY_TIMEOUT` | `45` | Tavily 搜索超时秒数 |
| `BROWSER_TOOL_TIMEOUT` | `60` | 浏览器工具超时秒数 |

## 10. 嵌入搜索（语义记忆）

> 使用 `MINIAGENT_EMBED_BASE_URL` / `MINIAGENT_EMBED_MODEL` 配置专用的 embedding 服务。
> 未配置时不进行向量搜索，仅用关键词倒排索引。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MINIAGENT_EMBED_SEARCH` | `0` | `1`/`true` 开启嵌入搜索 |
| `MINIAGENT_EMBED_BASE_URL` | *(空)* | 专用 embedding 服务 URL |
| `MINIAGENT_EMBED_MODEL` | *(空)* | 专用 embedding 模型（如 `text-embedding-3-small`） |
| `MINIAGENT_EMBED_DIM` | `1536` | 向量维度（通常无需手动设置） |
| `MINIAGENT_EMBED_TOP_K` | `8` | 最多返回记忆条目数 |
| `MINIAGENT_EMBED_MIN_SCORE` | `0.3` | 最低余弦相似度阈值 |
| `MINIAGENT_EMBED_API_KEY` | *(空)* | embedding 服务 API 密钥（使用专用服务时需要） |
| `MINIAGENT_EMBED_MAX_ENTRIES` | `10000` | 向量存储最大条目数 |

## 11. CLI / 状态 / 调试 / MCP

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MINIAGENT_SELF_OPT_TOOLS` | `1` | ~~自优化工具~~（已失效；参见 [SELF_OPT.md](SELF_OPT.md)） |
| `MINIAGENT_CLI_DOT_TOOLS` | `1` | CLI 点命令工具 |
| `MINIAGENT_CLI_RAW_MARKDOWN` | `0` | CLI 原始 Markdown 输出 |
| `MINIAGENT_CLI_THINKING_RICH` | `0` | CLI 富文本 thinking 展示 |
| `MINIAGENT_WELCOME_CLI_HINT` | `1` | CLI 启动提示 |
| `MINIAGENT_SKILLS_WATCH` | `0` | 监视技能目录变更并自动 refresh |
| `MINIAGENT_ALLOWED_COMMANDS` | *(空)* | exec 工具命令白名单（逗号分隔） |
| `MINI_AGENT_STATE` | `workspaces` | 运行时状态目录 |
| `MINI_AGENT_WORKSPACE` | *(空)* | 工作区路径 |
| `MINI_AGENT_SKILLS` | *(空)* | 技能包路径 |
| `MINI_AGENT_CONTEXT_TOOL_REDACT` | `1` | 上下文工具脱敏 |
| `MINI_AGENT_TUI_VERBOSE_LOG` | `0` | TUI 下允许 INFO/DEBUG 日志（默认提升至 WARNING） |
| `MINIAGENT_DEBUG_SESSION_ID` | *(空)* | 设置后启用 NDJSON 调试日志 |
| `MINIAGENT_DEBUG_LOG_PATH` | *(自动生成)* | 调试日志文件路径 |
| `MINIAGENT_MCP_STDIO` | *(空)* | MCP stdio 服务器命令，JSON 数组字符串 |
