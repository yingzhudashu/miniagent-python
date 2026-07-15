"""飞书多维表格（Bitable）客户端。"""

from miniagent.assistant.feishu.bitable.client import (
    create_record,
    delete_record,
    delete_records_batch,
    get_app_meta,
    get_record,
    list_fields,
    list_records,
    list_tables,
    update_record,
    upload_record_attachment,
)

__all__ = [
    "create_record",
    "delete_record",
    "delete_records_batch",
    "get_app_meta",
    "get_record",
    "list_fields",
    "list_records",
    "list_tables",
    "update_record",
    "upload_record_attachment",
]
