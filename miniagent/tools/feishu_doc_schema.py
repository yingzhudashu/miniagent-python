"""飞书云文档聚合工具的函数调用 schema。"""

from __future__ import annotations


def build_feishu_doc_schema(actions: tuple[str, ...]) -> dict[str, object]:
    """根据受支持动作构造稳定的函数调用声明。"""
    return {
        "type": "function",
        "function": {
            "name": "feishu_doc",
            "description": (
                "飞书云文档（docx）统一工具。action："
                "create/get/read/write/append/delete、list_blocks/get_block/update_block/delete_block/batch_update、"
                "export_raw/import_raw；表格 create_table/write_table_cells/create_table_with_values；"
                "媒体 upload_image/upload_file/download_media/upload_image_from_message；"
                "copy/move；list_permissions/add_permission/remove_permission；search（需 User Token）。"
                "write 默认 append；mode=replace 整篇替换。doc_token 可为 document_id 或 docx URL。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": list(actions), "description": "操作类型"},
                    "doc_token": {"type": "string", "description": "文档 ID 或 docx URL"},
                    "document_id": {"type": "string", "description": "同 doc_token"},
                    "title": {"type": "string"}, "folder_token": {"type": "string"},
                    "owner_open_id": {"type": "string", "description": "创建时建议传入用户 open_id"},
                    "content": {"type": "string", "description": "write/append/update_block 正文"},
                    "text": {"type": "string", "description": "append 别名"},
                    "render_mode": {
                        "type": "string", "enum": ["rich", "plain"], "default": "rich",
                        "description": "渲染模式：rich=富文本渲染，plain=纯文本",
                    },
                    "block_id": {"type": "string"}, "page_token": {"type": "string"},
                    "requests": {"description": "batch_update 请求数组或 JSON 字符串"},
                    "relative_path": {
                        "type": "string",
                        "description": "export_raw/import_raw/download_media/upload_image 等工作区相对路径",
                    },
                    "path": {"type": "string", "description": "relative_path 别名"},
                    "mode": {"type": "string", "description": "write 时：replace 整篇替换，默认 append"},
                    "table_block_id": {"type": "string", "description": "write_table_cells"},
                    "values": {"description": "表格二维数组或 JSON 字符串"},
                    "row_size": {"type": "integer", "description": "create_table"},
                    "column_size": {"type": "integer", "description": "create_table"},
                    "parent_block_id": {"type": "string", "description": "create_table 父块"},
                    "file_token": {"type": "string", "description": "download_media"},
                    "extra": {"type": "string", "description": "download_media 可选 extra"},
                    "message_id": {"type": "string", "description": "upload_image_from_message"},
                    "file_key": {"type": "string", "description": "upload_image_from_message"},
                    "name": {"type": "string", "description": "copy 新文档名"},
                    "member_type": {"type": "string", "description": "add/remove_permission"},
                    "member_id": {"type": "string", "description": "协作者 ID 或 email"},
                    "email": {"type": "string", "description": "add_permission 别名"},
                    "open_id": {"type": "string", "description": "add_permission 别名"},
                    "perm": {"type": "string", "description": "view/edit/full_access 等"},
                    "query": {"type": "string", "description": "search 关键词"},
                    "q": {"type": "string", "description": "query 别名"},
                },
                "required": ["action"],
            },
        },
    }


__all__ = ["build_feishu_doc_schema"]
