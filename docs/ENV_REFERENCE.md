# 环境变量参考

本文档列出所有影响 miniagent-python 运行时行为的环境变量。

> 提示：复制 `.env.example` 为 `.env` 后填写；勿提交真实密钥。

---

## 1. 必填凭据

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OPENAI_API_KEY` | *(空)* | LLM API 密钥，启动必须 |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | API 基础 URL，可指向兼容 OpenAI 的第三方服务 |
| `OPENAI_MODEL` | `gpt-4o-mini` | 主模型名称 |
| `FEISHU_APP_ID` | *(空)* | 飞书应用 ID（`--feishu` 启动时必须） |
| `FEISHU_APP_SECRET` | *(空)* | 飞书应用 Secret |
| `FEISHU_VERIFICATION_TOKEN` | *(空)* | 飞书事件订阅验证 Token |
| `FEISHU_ENCRYPT_KEY` | *(空)* | 飞书事件加密 Key（可选） |
| `TAVILY_API_KEY` | *(空)* | Web 搜索 API 密钥（`web_search` 工具需要） |

## 2. 模型行为

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MODEL_PROFILE` | `balanced` | 模型预设：`creative` / `balanced` / `precise` / `code` / `fast` |
| `AGENT_CONTEXT_WINDOW` | `128000` | 上下文窗口大小（token 数） |
| `OPENAI_MAX_TOKENS` | *(空)* | 最大输出 token 数 |
| `AGENT_THINKING_DEFAULT` | `medium` | 思考档位：见 `thinking_presets.py` |
| `OPENAI_THINKING_BUDGET` | *(空)* | 思考预算 token（若与 AGENT_THINKING_DEFAULT 同设，以此为准） |

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
| `MINIAGENT_ANNOUNCE_DIFFICULTY_AND_PLAN` | `1` | 是否向用户展示任务难度与规划摘要 |
| `LOOP_DETECTION_ENABLED` | `true` | 是否启用循环检测 |
| `LOOP_HISTORY_SIZE` | `50` | 循环检测历史窗口大小 |
| `LOOP_WARNING_THRESHOLD` | `8` | 循环检测警告阈值 |
| `LOOP_CRITICAL_THRESHOLD` | `12` | 循环检测临界阈值 |

## 4. 飞书工具与体验

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MINIAGENT_FEISHU_REPLY_PLAIN` | `1` | `1` 时最终回复简化部分 Markdown |
| `MINIAGENT_FEISHU_REPLY_TARGET` | `reply` | `reply`=回复入站消息；`create`=新建消息 |
| `MINIAGENT_FEISHU_CARD_ACTION_ROUTER` | `1` | `1` 时注册卡片按钮回调 |
| `MINIAGENT_FEISHU_TOOLS_AUTO` | `1` | 未设 `MINIAGENT_FEISHU_TOOLS` 时，`1` 且已配凭据则自动注册飞书工具 |
| `MINIAGENT_FEISHU_TOOLS` | *(空)* | 显式控制飞书工具注册：`1` 启用 |
| `MINIAGENT_FEISHU_DOT_COMMANDS_FULL` | `0` | `1` 时飞书点命令与 CLI 同等功能 |
| `FEISHU_DOC_FOLDER_FALLBACK_ROOT_META` | `1` | 无 folder_token 时尝试根目录元数据 API |
| `MINIAGENT_FEISHU_TABLE_FALLBACK` | `both` | GFM 表回退策略 |
| `MINIAGENT_FEISHU_LARK_TABLE_MAX_PIPES` | `14` | lark_md 表最大列数 |
| `MINIAGENT_FEISHU_CARD_EXTRACT_INBOUND` | `1` | 入站 interactive 消息抽取可读文本 |
| `MINIAGENT_FEISHU_CARD_V2` | `0` | `1` 时超宽 GFM 表额外发送 schema 2.0 卡片 |
| `MINIAGENT_FEISHU_CARD_V2_MAX_ROWS` | `20` | V2 卡片最大行数 |
| `MINIAGENT_FEISHU_CARD_V2_MAX_COLS` | `8` | V2 卡片最大列数 |
| `MINIAGENT_FEISHU_USER_ACCESS_TOKEN` | *(空)* | 用户 OAuth token；`feishu_doc action=search` 需要 |
| `MINIAGENT_FEISHU_MARKDOWN_COMMANDS` | `0` | 飞书 Markdown 命令支持 |
| `MINIAGENT_FEISHU_REPLY_IN_THREAD` | `0` | 与 `REPLY_TARGET=reply` 联用，话题内回复 |
| `MINIAGENT_FEISHU_RECEIVE_ID_TYPE` | `chat_id` | 接收消息 ID 类型 |
| `MINIAGENT_FEISHU_DOCX_URL_PREFIX` | *(空)* | 文档 URL 前缀 |
| `MINIAGENT_FEISHU_DOC_FOLDER_TOKEN` | *(空)* | 文档目录 token |
| `MINIAGENT_FEISHU_MEDIA_RUN_AGENT` | `0` | 媒体消息触发 Agent 运行 |
| `MINIAGENT_FEISHU_MEDIA_SILENT_REPLY` | `0` | 媒体消息静默回复 |

### 飞书内部调优（一般不需修改）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MINI_AGENT_THINKING_FOR_LLM_MAX_CHARS` | `10000` | 飞书卡片中 thinking 正文最大字符 |
| `MINI_AGENT_FEISHU_CARD_BODY_MAX` | `48000` | 飞书卡片 body 最大字符 |

## 5. 飞书 WebSocket 长连接

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MINIAGENT_FEISHU_WS_AUTO_RECONNECT` | `0` | 自动重连 |
| `MINIAGENT_FEISHU_WS_WATCHDOG_INTERVAL_S` | `30` | 看门狗检测间隔（秒） |
| `MINIAGENT_FEISHU_WS_DEAD_CONN_GRACE_S` | `90` | 死连接宽限期（秒） |
| `MINIAGENT_FEISHU_WS_RECONNECT_GRACE_S` | `300` | 重连宽限期（秒） |
| `MINIAGENT_FEISHU_WS_REFRESH_INTERVAL_S` | `0` | 刷新间隔（秒） |
| `MINIAGENT_FEISHU_WS_IDLE_REFRESH_S` | `0` | 空闲刷新间隔（秒） |

## 6. 定时任务

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MINIAGENT_DISABLE_SCHEDULED_TASKS` | `0` | `1` 时禁用定时任务 |
| `MINIAGENT_SCHEDULE_DISPATCH_BACKOFF` | `60` | 任务调度退避秒数 |
| `MINIAGENT_SCHEDULE_TIMEZONE` | *(跟随系统)* | 定时任务专用时区 |
| `MINIAGENT_SCHEDULE_FEISHU_MIRROR` | `1` | 定时任务飞书镜像 |
| `MINIAGENT_SCHEDULE_FEISHU_LAST_CHAT` | `0` | 定时任务最近聊天 |
| `MINIAGENT_SCHEDULE_TOOLS` | `1` | 定时任务工具注册 |

## 7. 时区

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `TZ` | *(系统时区)* | 标准时区环境变量，如 `Asia/Shanghai` |
| `MINIAGENT_TIMEZONE` | *(跟随 TZ)* | miniagent 时区配置，与 TZ 二选一 |

## 8. CLI / 状态 / 调试

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MINIAGENT_SELF_OPT_TOOLS` | `1` | 自优化工具 |
| `MINIAGENT_CLI_DOT_TOOLS` | `1` | CLI 点命令工具 |
| `MINIAGENT_CLI_RAW_MARKDOWN` | `0` | CLI 原始 Markdown 输出 |
| `MINIAGENT_CLI_THINKING_RICH` | `0` | CLI 富文本 thinking 展示 |
| `MINIAGENT_WELCOME_CLI_HINT` | `1` | CLI 启动提示 |
| `MINIAGENT_SKILLS_WATCH` | `0` | 监视技能目录变更并自动 refresh |
| `MINIAGENT_DEBUG_SESSION_ID` | *(空)* | 调试 session ID，设置后启用 NDJSON 调试日志 |
| `MINIAGENT_DEBUG_LOG_PATH` | *(自动生成)* | 调试日志文件路径 |

## 9. MCP（模型上下文协议）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MINIAGENT_MCP_STDIO` | *(空)* | MCP stdio 服务器命令，JSON 数组字符串，如 `["npx","-y","@some/mcp-server"]` |

## 10. 工作区与路径

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MINI_AGENT_STATE` | `workspaces` | 运行时状态目录 |
| `MINI_AGENT_WORKSPACE` | *(空)* | 工作区路径 |
| `MINI_AGENT_SKILLS` | *(空)* | 技能包路径 |
| `MINI_AGENT_CONTEXT_TOOL_REDACT` | `1` | 上下文工具脱敏 |

## 11. Web 搜索

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `WEB_SEARCH_API_KEY` | *(空)* | Web 搜索备用 API Key（与 `TAVILY_API_KEY` 任一即可） |
| `TAVILY_TIMEOUT` | `45` | Tavily 搜索超时秒数 |
| `BROWSER_TOOL_TIMEOUT` | `60` | 浏览器工具超时秒数 |
