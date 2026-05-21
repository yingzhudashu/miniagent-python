# 飞书工具 API 参考

本文档列出所有飞书集成相关工具的完整 action 列表、参数和返回值。

---

## feishu_doc（飞书云文档聚合工具）

单一工具，通过 `action` 参数执行 26 种操作。

### 基础操作

| Action | 参数 | 返回值 | 权限 |
|--------|------|--------|------|
| `create` | `title` (文档标题), `folder_token` (可选，父目录 token), `folder_share_url` (可选，目录分享 URL 代替 token) | `{document_id, document_url, revision}` | 需云文档创建权限 |
| `get` | `document_id` | 文档元数据 | 需文档访问权限 |
| `read` | `document_id` | `{content, block_type_counts}` 结构化内容 | 需文档读取权限 |
| `write` | `document_id`, `text`, `mode` (`append` 或 `replace`, 默认 `append`) | 写入结果 | 需文档编辑权限 |
| `append` | `document_id`, `text` | 追加结果（`write` 的 `mode=append` 别名） | 需文档编辑权限 |
| `delete` | `document_id` | 删除结果 | 需文档管理权限 |

### Block 操作

| Action | 参数 | 返回值 | 权限 |
|--------|------|--------|------|
| `list_blocks` | `document_id` | Block 列表（类型、ID） | 需文档读取权限 |
| `get_block` | `document_id`, `block_id` | 单个 Block 详情 | 需文档读取权限 |
| `update_block` | `document_id`, `block_id`, `text` | 更新结果 | 需文档编辑权限 |
| `delete_block` | `document_id`, `block_id` | 删除结果 | 需文档编辑权限 |
| `batch_update` | `document_id`, `operations` (JSON 数组) | 批量操作结果 | 需文档编辑权限 |

### 导入/导出

| Action | 参数 | 返回值 | 权限 |
|--------|------|--------|------|
| `export_raw` | `document_id` | 文档原始导出内容 | 需文档导出权限 |
| `import_raw` | `document_id`, `raw_content` | 导入结果 | 需文档编辑权限 |

### 表格操作

| Action | 参数 | 返回值 | 权限 |
|--------|------|--------|------|
| `create_table` | `document_id`, `rows`, `cols` | 创建的 table block ID | 需文档编辑权限 |
| `write_table_cells` | `document_id`, `table_block_id`, `cells` | 写入结果 | 需文档编辑权限 |
| `create_table_with_values` | `document_id`, `headers`, `rows` | 创建并填充的 table block ID | 需文档编辑权限 |

### 媒体操作

| Action | 参数 | 返回值 | 权限 |
|--------|------|--------|------|
| `upload_image` | `document_id`, `image_path` (工作区内相对路径) | 上传结果 | 需文档编辑权限 |
| `upload_file` | `document_id`, `file_path` (工作区内相对路径) | 上传结果 | 需文档编辑权限 |
| `download_media` | `document_id`, `media_token` | 媒体文件字节流 | 需文档读取权限 |
| `upload_image_from_message` | `document_id` | 从消息附件上传图片结果 | 需文档编辑权限 |

### 云盘操作

| Action | 参数 | 返回值 | 权限 |
|--------|------|--------|------|
| `copy` | `document_id`, `target_folder_token` | 复制结果 | 需源读取 + 目标编辑权限 |
| `move` | `document_id`, `target_folder_token` | 移动结果 | 需源管理 + 目标编辑权限 |

### 协作者管理

| Action | 参数 | 返回值 | 权限 |
|--------|------|--------|------|
| `list_permissions` | `document_id` | 协作者列表 | 需文档管理权限 |
| `add_permission` | `document_id`, `member_type`, `member_id`, `permission` | 添加结果 | 需文档管理权限 |
| `remove_permission` | `document_id`, `member_type`, `member_id` | 移除结果 | 需文档管理权限 |

### 搜索

| Action | 参数 | 返回值 | 权限 |
|--------|------|--------|------|
| `search` | `query` | 搜索结果列表 | 需 `MINIAGENT_FEISHU_USER_ACCESS_TOKEN` |

---

## feishu_bitable（飞书多维表格聚合工具）

单一工具，通过 `action` 参数执行 8 种操作。

| Action | 参数 | 返回值 | 权限 |
|--------|------|--------|------|
| `get_meta` | `app_token` (从 `base/<token>` URL 提取) | 应用元数据（名称、tables） | 需多维表格访问权限 |
| `list_fields` | `app_token`, `table_id` | 字段列表（名称、类型） | 需多维表格读取权限 |
| `list_records` | `app_token`, `table_id`, `page_token` (可选), `field_names` (可选) | 记录列表（支持分页） | 需多维表格读取权限 |
| `get_record` | `app_token`, `table_id`, `record_id` | 单条记录详情 | 需多维表格读取权限 |
| `create_record` | `app_token`, `table_id`, `fields` (JSON 对象) | 创建结果（含 `record_id`） | 需多维表格编辑权限 |
| `update_record` | `app_token`, `table_id`, `record_id`, `fields` | 更新结果 | 需多维表格编辑权限 |
| `delete_record` | `app_token`, `table_id`, `record_id` | 删除结果 | 需多维表格编辑权限 |
| `upload_attachment` | `app_token`, `table_id`, `record_id`, `field_name`, `file_path` (工作区内相对路径) | 上传结果 | 需多维表格编辑权限 |

---

## feishu_send_interactive_card

发送交互式卡片到飞书会话。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `receive_id` | str | 是 | 接收方 ID（chat_id 或 open_id） |
| `template` | str | 是 | 卡片模板名称 |
| `data` | dict | 否 | 卡片模板变量 |
| `receive_id_type` | str | 否 | ID 类型，默认 `chat_id` |

---

## feishu_list_drive_files

列出飞书云盘文件/目录。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `folder_token` | str | 否 | 目录 token，不指定则列根目录 |
| `folder_share_url` | str | 否 | 目录分享 URL（与 folder_token 二选一） |

---

## feishu_recall_message

撤回飞书消息。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `message_id` | str | 是 | 待撤回消息 ID |

---

## feishu_send_workspace_file

发送工作区内文件到飞书会话。

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_path` | str | 是 | 相对于会话工作区的路径 |
