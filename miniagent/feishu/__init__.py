"""飞书通道适配层（WebSocket 长轮询、消息类型、可选 HTTP Webhook）。

运行时任务封装在 ``miniagent.engine.feishu_state.FeishuRuntime``；本包提供与飞书 API 交互的
实现模块，由引擎在启动或 ``.feishu start`` 时加载。

主要模块：
- ``poll_server``：长连接与消息派发
- ``agent_handler``：将飞书事件转为与 CLI 统一的处理路径
- ``types``：配置与事件/回复数据结构
- ``server``：Webhook 相关（若部署该形态）
"""

__all__: list[str] = []
