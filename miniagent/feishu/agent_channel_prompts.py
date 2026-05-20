"""飞书入站通道下合并到 Agent system 的短提示（避免模型误称无 API）。"""

from __future__ import annotations

from typing import Any

from miniagent.feishu.im_tool_policy import feishu_credentials_configured
from miniagent.tools.feishu_im_tools import FEISHU_IM_TOOL_NAMES

_FEISHU_CHANNEL_HINT_WITH_TOOLS = """## 飞书通道（扩展内置工具已启用）

当前消息来自飞书。你已具备飞书开放平台相关**内置工具**，在适用场景必须**实际调用工具**，不要声称「未集成飞书 API」「无法创建云文档或发附件」：

- `feishu_create_document` / `feishu_append_document_text` / `feishu_get_document_markdown`：云文档；`folder_token` 可为**纯 token**或**云盘文件夹分享链接**，也可依赖环境变量默认父目录；可选 `FEISHU_DOC_FOLDER_FALLBACK_ROOT_META=1` 在无配置时调用根目录元数据 API（须 drive 权限，默认关闭）。工具失败信息中会说明已尝试的来源，请先阅读再向用户索要补充信息。
- `feishu_list_drive_files`：列举云盘文件夹（只读）；`folder_token` 解析规则与创建文档相同，可省略参数若已配置默认目录或启用根目录回退。
- `feishu_send_workspace_file`：将**当前会话工作区根目录**下的文件以 IM 文件/图片发出；参数 `relative_path` 为相对该根的路径（例如 `files/feishu_incoming/截图_msgid.png`）。用户经飞书发来的附件由系统保存到 `files/feishu_incoming/`，优先使用该路径。
- `feishu_recall_message`：撤回机器人已发消息。

若用户指「本机任意路径」的文件，须先用工作区内的读写工具把内容落到会话 `files/` 下再调用发送工具；不要编造不存在的相对路径。"""

_FEISHU_CHANNEL_HINT_WITHOUT_TOOLS = """## 飞书通道（扩展内置工具未启用）

当前消息来自飞书，但本进程**未注册**飞书扩展内置工具（发工作区文件、建云文档等）。请勿虚构已成功调用飞书开放平台接口。

请如实说明：需在运行环境开启 `MINIAGENT_FEISHU_TOOLS=1`，或 **不设置** `MINIAGENT_FEISHU_TOOLS` 且 `MINIAGENT_FEISHU_TOOLS_AUTO=1`（默认，且已配置 `FEISHU_APP_ID`/`SECRET`）；并配置开放平台权限与可选 `MINIAGENT_FEISHU_DOCX_URL_PREFIX` 等；详见仓库 `docs/FEISHU.md`「飞书工具与 IM 自检清单」。"""


def registry_has_any_feishu_im_tool(registry: Any) -> bool:
    """若注册表中已存在任一飞书 IM/Doc 扩展内置工具名则返回真。"""
    get = getattr(registry, "get", None)
    if not callable(get):
        return False
    for name in FEISHU_IM_TOOL_NAMES:
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
    if registry_has_any_feishu_im_tool(registry):
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
    "registry_has_any_feishu_im_tool",
]
