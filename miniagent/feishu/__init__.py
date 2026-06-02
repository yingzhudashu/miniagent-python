"""飞书通道适配层（WebSocket 长连接、消息类型、可选 HTTP Webhook）。

运行时任务封装在 ``miniagent.engine.feishu_state.FeishuRuntime``；本包提供与飞书 API 交互的
实现模块，由引擎在启动或 ``.feishu start`` 时加载。底层 SDK 为可选依赖：需
``pip install miniagent-python[feishu]``（``lark-oapi``）后 ``poll_server`` 等路径方可完整运行。

主要模块：

- ``poll_server``：长连接、消息派发、去重与防抖、卡片渲染
- ``resource_io``：消息内 file/image 资源下载（依赖 lark-oapi）
- ``upload_io``：IM 素材上传与 file/image 消息发送
- ``im_send``：IM 创建/回复消息的统一发送入口
- ``im_tool_policy``：飞书工具启用策略
- ``feishu_tool_policy``：飞书工具注册策略（toolbox 控制）
- ``lark_client`` / ``token_resolve``：SDK 客户端与 docx/base URL 解析
- ``lark_response``：开放平台错误摘要
- ``docx/``：云文档块级读写（``feishu_doc`` 工具后端）
- ``bitable/``：多维表格 CRUD（``feishu_bitable`` 工具后端）
- ``drive_client`` / ``drive_extra``：云盘文件夹列举
- ``folder_token_resolve``：父目录 token 解析
- ``ws_client`` / ``ws_health``：WebSocket 客户端与健康监控
- ``receive_id``：接收消息 ID 类型处理
- ``agent_channel_prompts``：Agent 通道提示模板
- ``types``：配置与事件/回复数据结构

运维与安全清单见 ``docs/FEISHU.md``、``docs/SECURITY.md``；入站锁见 ``feishu_inbound_lock``。
"""

# ── 核心客户端 ──
from miniagent.feishu.lark_client import build_client, clear_client_cache, config_from_env

# ── IM 发送 ──
from miniagent.feishu.im_send import ImMsgType, post_im_message

# ── 云盘 ──
from miniagent.feishu.drive_client import (
    LIST_FILE_PAGE_SIZE,
    get_root_folder_meta,
    list_folder_files_page,
)

# ── 卡片 ──
from miniagent.feishu.cards import (
    build_button,
    build_interactive_card,
    build_v2_table_card,
    reply_card_dict,
    thinking_card_dict,
)

# ── 文档 ──
from miniagent.feishu.docx import (
    DOCX_APPEND_MAX_BLOCKS,
    DOCX_APPEND_MAX_CHARS,
    append_plain_text_to_document,
    create_document,
    get_document,
)

# ── 多维表格 ──
from miniagent.feishu.bitable import (
    get_app_meta,
    list_records,
    create_record,
    update_record,
)

__all__ = [
    # 核心客户端
    "build_client",
    "clear_client_cache",
    "config_from_env",
    # IM
    "ImMsgType",
    "post_im_message",
    # 云盘
    "LIST_FILE_PAGE_SIZE",
    "get_root_folder_meta",
    "list_folder_files_page",
    # 卡片
    "build_button",
    "build_interactive_card",
    "build_v2_table_card",
    "reply_card_dict",
    "thinking_card_dict",
    # 文档
    "DOCX_APPEND_MAX_BLOCKS",
    "DOCX_APPEND_MAX_CHARS",
    "append_plain_text_to_document",
    "create_document",
    "get_document",
    # 多维表格
    "get_app_meta",
    "list_records",
    "create_record",
    "update_record",
]
