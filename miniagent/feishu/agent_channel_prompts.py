"""飞书入站通道下合并到 Agent system 的短提示（避免模型误称无 API）。"""

from __future__ import annotations

from typing import Any

from miniagent.feishu.feishu_tool_policy import FEISHU_EXT_TOOL_NAMES
from miniagent.feishu.im_tool_policy import feishu_credentials_configured

_FEISHU_CHANNEL_HINT_WITH_TOOLS = """## 飞书通道（扩展内置工具已启用）

当前消息来自飞书。你已具备飞书开放平台**内置工具**，在适用场景必须**实际调用工具**，不要声称「未集成飞书 API」：

- **`feishu_doc`**（`action`）：云文档 create/get/read/write（`mode=replace` 整篇替换）/append/delete、块 list_blocks/get_block/update_block/delete_block/batch_update、export_raw/import_raw；表格 create_table/write_table_cells/create_table_with_values；媒体 upload_image/upload_file/download_media/upload_image_from_message；云盘 copy/move；协作 list_permissions/add_permission/remove_permission；发现 search（需 `secrets.feishu_user_access_token`）。`doc_token` 可为 document_id 或 docx URL。
- **`feishu_bitable`**（`action`）：get_meta、list_fields、list_records、get_record、create_record、update_record、delete_record、upload_attachment（工作区相对路径）。`app_token` 可为 token 或 base URL。
- **`feishu_list_drive_files`**：列举云盘文件夹（只读）。
- **`feishu_send_workspace_file`**：发送会话工作区 `files/` 下文件（如 `files/feishu_incoming/…`）。
- **`feishu_recall_message`**：撤回机器人消息。
- **`feishu_send_interactive_card`** / **`feishu_update_message_card`**：发送或更新交互卡片；按钮 `value` 建议含 `miniagent_text`、`chat_id`，可选 `action_id`、`dedupe_key`（防连点，需 `feishu.card_action_router=true`）。

写 Bitable 记录前请先 `feishu_bitable` + `action=list_fields`。读云文档含表格/图片时 `read` 后按 `hint` 用 `list_blocks`。"""

_FEISHU_CHANNEL_HINT_WITHOUT_TOOLS = """## 飞书通道（扩展内置工具未启用）

当前消息来自飞书，但本进程**未注册**飞书扩展内置工具。请勿虚构已成功调用飞书开放平台接口。

请开启 `feishu.tools_explicit=true`，或保持 `feishu.tools_auto=true`（并已配置 `secrets.feishu_*`）；详见 `docs/FEISHU.md`。"""


def registry_has_any_feishu_ext_tool(registry: Any) -> bool:
    """注册表中是否存在任一飞书扩展内置工具。"""
    get = getattr(registry, "get", None)
    if not callable(get):
        return False
    for name in FEISHU_EXT_TOOL_NAMES:
        if get(name) is not None:
            return True
    return False


def append_feishu_channel_system(
    base: str | None,
    *,
    is_feishu: bool,
    registry: Any,
) -> str | None:
    """在飞书通道下追加短 system 段；非飞书或未注入 registry 时原样返回。"""
    if not is_feishu or registry is None:
        return base
    if registry_has_any_feishu_ext_tool(registry):
        extra = _FEISHU_CHANNEL_HINT_WITH_TOOLS
    else:
        if not feishu_credentials_configured():
            return base
        extra = _FEISHU_CHANNEL_HINT_WITHOUT_TOOLS
    if not base or not base.strip():
        return extra
    return f"{base.rstrip()}\n\n{extra}"


__all__ = [
    "append_feishu_channel_system",
    "registry_has_any_feishu_ext_tool",
]
