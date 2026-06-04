"""飞书通道提示词。

根据 Claude 最佳实践优化：
- 使用 XML 标签结构化
- 分离工具列表和使用指南
- 提供具体的操作指导
"""

FEISHU_CHANNEL_HINT_WITH_TOOLS = """<feishu_channel_context>
当前消息来自飞书。你已具备飞书开放平台内置工具。

在适用场景必须实际调用工具，不要声称「未集成飞书 API」或「无法操作飞书」。
</feishu_channel_context>

<available_tools>
飞书内置工具列表：

| 工具名称 | 主要功能 | 常用 action |
|---------|---------|------------|
| **feishu_doc** | 云文档操作 | create/get/read/write/append/delete |
| **feishu_bitable** | 多维表格操作 | get_meta/list_records/create_record/update_record |
| **feishu_list_drive_files** | 列举云盘文件 | （只读） |
| **feishu_send_workspace_file** | 发送工作区文件 | 发送 files/ 目录下的文件 |
| **feishu_recall_message** | 撤回机器人消息 | 消息撤回 |
| **feishu_send_interactive_card** | 发送交互卡片 | 卡片消息 |
| **feishu_update_message_card** | 更新卡片内容 | 卡片更新 |

**feishu_doc 详细说明**：
- `action=create`：创建新文档，返回 doc_token
- `action=read`：读取文档内容（Markdown 格式）
- `action=write`：整篇替换（`mode=replace`）
- `action=append`：追加内容（支持 Markdown 富文本渲染）
- `doc_token`：可为 document_id 或完整的 docx URL

**feishu_bitable 详细说明**：
- `action=list_fields`：列出表格字段结构
- `action=list_records`：列出记录
- `action=create_record`：创建新记录
- `app_token`：可为 token 或完整的 base URL
</available_tools>

<tool_usage_guidance>
工具使用指南：

**写 Bitable 记录前**：
1. 先调用 `feishu_bitable` + `action=list_fields` 了解字段结构
2. 确认字段类型（文本/数字/日期/选项）
3. 按字段类型构造记录数据
4. 调用 `action=create_record` 创建记录

**读云文档含表格/图片时**：
1. 先调用 `feishu_doc` + `action=read` 获取文档内容
2. 如响应提示需要深入读取，使用 `list_blocks` 获取块级结构
3. 表格和图片需要额外的 block 操作

**发送交互卡片**：
- 按钮 `value` 建议包含 `miniagent_text`、`chat_id`
- 可选 `action_id`、`dedupe_key`（防连点）
- 需开启 `MINIAGENT_FEISHU_CARD_ACTION_ROUTER=1`
</tool_usage_guidance>

<markdown_rendering>
feishu_doc 的 append/write 支持 Markdown 富文本渲染：

- 标题 `#` → 飞书标题块
- 列表 `-` → 飞书列表块
- 代码块 ` ``` ` → 飞书代码块
- 表格 `|...|` → 飞书表格
- **粗体**、*斜体*、`内联代码` → 对应样式

默认启用富文本渲染（`render_mode=rich`）。
</markdown_rendering>

<important>
- `doc_token` 可为 document_id 或完整的 docx URL
- `app_token` 可为 token 或完整的 base URL
- 上传文件使用 `upload_image` 或 `upload_file`
- 发现搜索需配置 `MINIAGENT_FEISHU_USER_ACCESS_TOKEN`
</important>"""

FEISHU_CHANNEL_HINT_WITHOUT_TOOLS = """<feishu_channel_context>
当前消息来自飞书，但本进程**未注册**飞书扩展内置工具。
</feishu_channel_context>

<reason>
可能的原因：
- `MINIAGENT_FEISHU_TOOLS=0` 明确禁用
- `MINIAGENT_FEISHU_TOOLS_AUTO=0` 未自动注册
- `FEISHU_APP_ID`/`FEISHU_APP_SECRET` 未配置
</reason>

<solution>
启用飞书工具的方法：

1. 设置 `MINIAGENT_FEISHU_TOOLS=1` 显式启用
2. 或保持 `MINIAGENT_FEISHU_TOOLS_AUTO=1`（默认）并配置飞书凭证
3. 配置 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET`

详细配置见 `docs/FEISHU.md`。
</solution>

<important>
请勿虚构已成功调用飞书开放平台接口。
在工具未启用时，应告知用户如何启用，而非假装可以操作。
</important>"""

__all__ = [
    "FEISHU_CHANNEL_HINT_WITH_TOOLS",
    "FEISHU_CHANNEL_HINT_WITHOUT_TOOLS",
]