"""飞书通道适配层（WebSocket 长轮询、消息类型、可选 HTTP Webhook）。

运行时任务封装在 ``miniagent.engine.feishu_state.FeishuRuntime``；本包提供与飞书 API 交互的
实现模块，由引擎在启动或 ``.feishu start`` 时加载。底层 SDK 为可选依赖：需
``pip install miniagent-python[feishu]``（``lark-oapi``）后 ``poll_server`` 等路径方可完整运行。

主要模块：

- ``poll_server``：长连接与消息派发、去重与防抖
- ``agent_handler``：将飞书事件转为与 CLI 统一的处理路径（闭包注入队列/引擎）
- ``resource_io``：消息内 file/image 资源下载（依赖 lark-oapi）
- ``upload_io``：IM 素材上传与 file/image 消息发送
- ``im_send``：IM 创建/回复消息的统一发送入口（供 ``poll_server`` / ``upload_io`` 复用）
- ``docx_client``：云文档 docx 创建与 raw_content 读取（需开放平台权限）
- ``docx_blocks``：docx 页面下追加纯文本段落（块级 API）
- ``drive_client``：云盘文件夹列举（folder_token）、可选根文件夹元数据（``get_root_folder_meta``）
- ``folder_token_resolve``：工具参数/URL/环境变量/根目录回退的父目录 token 解析
- ``lark_response``：开放平台错误摘要
- ``types``：配置与事件/回复数据结构
- ``server``：Webhook 相关（若部署该形态）

运维与安全清单见 ``docs/FEISHU.md``、``docs/SECURITY.md``；入站锁见 ``feishu_inbound_lock``。
"""

__all__: list[str] = []
