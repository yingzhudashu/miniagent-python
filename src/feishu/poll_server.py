"""Mini Agent Python — 飞书 WebSocket 长轮询服务器 (Phase 8)

使用飞书 SDK WSClient 长轮询模式接收事件推送。

核心机制（对齐 OpenClaw）：
- 单客户端单例：防止多实例导致事件路由不确定
- 内存+磁盘双重去重：防止重复处理同一消息
- 聊天室顺序队列：防止并发导致上下文混乱
- 消息防抖：合并同一发送者短时内的连续消息
- 优雅关闭：SIGINT/SIGTERM 信号处理

适用场景：
- 无需公网 IP，适合家庭网络或内网部署
- 飞书开放平台的企业自建应用
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Awaitable, Callable
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

from src.feishu.types import FeishuConfig
from src.core.logger import get_logger

_logger = get_logger(__name__)

# 去重配置
DEDUP_TTL_MS = 5 * 60 * 1000  # 5 分钟
DEDUP_MAX_SIZE = 2000

# 单例状态
_singleton_client: Any = None
_singleton_app_id: str | None = None

# 内存去重
_processing_claims: dict[str, float] = {}

# 磁盘去重
_state_dir = os.path.join(
    os.environ.get("MINI_AGENT_STATE", os.path.join(os.getcwd(), "state")),
    "feishu",
    "dedup",
)
_dedup_file = os.path.join(_state_dir, "processed.json")
_disk_dedup: dict[str, float] = {}


# ─── 去重管理 ───


def _ensure_state_dir():
    """确保状态目录存在。"""
    os.makedirs(_state_dir, exist_ok=True)


def _load_disk_dedup():
    """加载磁盘去重数据。"""
    global _disk_dedup
    try:
        _ensure_state_dir()
        if os.path.isfile(_dedup_file):
            with open(_dedup_file, "r", encoding="utf-8") as f:
                _disk_dedup = json.load(f)
    except Exception:
        _disk_dedup = {}


def _save_disk_dedup():
    """保存磁盘去重数据。"""
    try:
        _ensure_state_dir()
        with open(_dedup_file, "w", encoding="utf-8") as f:
            json.dump(_disk_dedup, f, indent=2)
    except Exception:
        pass


def _resolve_dedup_key(message_id: str) -> str:
    """解析去重键。"""
    return f"mini-agent:{message_id.strip()}"


def _prune_claims():
    """清理过期去重条目。"""
    cutoff = time.time() - DEDUP_TTL_MS / 1000.0
    to_remove = [k for k, v in _processing_claims.items() if v < cutoff]
    for k in to_remove:
        del _processing_claims[k]

    to_remove = [k for k, v in _disk_dedup.items() if v < cutoff]
    for k in to_remove:
        del _disk_dedup[k]

    if len(_processing_claims) + len(_disk_dedup) > DEDUP_MAX_SIZE * 2:
        _save_disk_dedup()


def try_begin_processing(message_id: str) -> bool:
    """尝试获取消息处理权。

    Returns:
        True = 首次处理，可以处理；False = 重复/处理中，跳过
    """
    key = _resolve_dedup_key(message_id)
    if not key:
        return True

    now = time.time()
    _prune_claims()

    # 1. 检查磁盘去重
    if key in _disk_dedup:
        return False

    # 2. 检查内存处理中
    if key in _processing_claims:
        return False

    # 获取处理权
    _processing_claims[key] = now
    _prune_claims()
    return True


def release_processing(message_id: str):
    """释放处理权 + 记录到磁盘去重。"""
    key = _resolve_dedup_key(message_id)
    if not key:
        return

    _processing_claims.pop(key, None)
    _disk_dedup[key] = time.time()

    # 限制磁盘去重大小
    if len(_disk_dedup) > DEDUP_MAX_SIZE:
        sorted_items = sorted(_disk_dedup.items(), key=lambda x: x[1])
        to_remove = len(sorted_items) // 5  # 删除最老的 20%
        for k, _ in sorted_items[:to_remove]:
            del _disk_dedup[k]
        _save_disk_dedup()


# 初始化磁盘去重
_load_disk_dedup()


# ─── 顺序队列 ───

_chat_queues: dict[str, list] = {}


def enqueue_chat_message(chat_id: str, fn) -> None:
    """将消息加入聊天室顺序队列。"""
    queue = _chat_queues.get(chat_id)
    if queue is None:
        queue = []
        _chat_queues[chat_id] = queue
    queue.append(fn)

    # 如果队列长度为 1，说明没有正在处理，立即开始
    if len(queue) == 1:
        asyncio.create_task(_process_chat_queue(chat_id))


async def _process_chat_queue(chat_id: str) -> None:
    """处理聊天室顺序队列。"""
    queue = _chat_queues.get(chat_id)
    if not queue:
        return

    fn = queue.pop(0)
    try:
        await fn()
    except Exception as e:
        _logger.warning("队列处理失败 [%s]: %s", chat_id, e)

    # 继续处理下一条
    if queue:
        asyncio.create_task(_process_chat_queue(chat_id))
    else:
        del _chat_queues[chat_id]


# ─── 消息防抖 ───

_debounce_timers: dict[str, asyncio.Task] = {}


def debounce_message(chat_id: str, fn, delay_ms: int = 1500) -> None:
    """对同一聊天室的消息进行防抖。

    1.5 秒内的连续消息，只处理最后一条。
    """
    existing = _debounce_timers.get(chat_id)
    if existing:
        existing.cancel()

    async def _delayed():
        await asyncio.sleep(delay_ms / 1000.0)
        _debounce_timers.pop(chat_id, None)
        try:
            await fn()
        except Exception as e:
            _logger.warning("防抖处理失败: %s", e)

    task = asyncio.create_task(_delayed())
    _debounce_timers[chat_id] = task


# ─── 飞书客户端 ───

async def start_feishu_poll_server(
    config: FeishuConfig,
    message_handler: Callable[[str, str, str], Awaitable[str]],
) -> None:
    """启动飞书 WebSocket 长轮询模式。

    建立与飞书服务器的 WebSocket 连接，
    持续接收事件推送并分发给消息处理器。

    Args:
        config: 飞书应用配置
        message_handler: 消息处理函数 (content, chatId, senderId) => reply
    """
    global _singleton_client, _singleton_app_id

    # 单客户端保护
    if _singleton_client and _singleton_app_id == config.app_id:
        _logger.info("已存在相同 appId 的 WSClient，复用现有连接")
        return

    if _singleton_client and _singleton_app_id != config.app_id:
        _logger.info("存在不同 appId 的 WSClient (%s)，先关闭", _singleton_app_id)
        await _singleton_client.close()
        _singleton_client = None
        _singleton_app_id = None

    # 加载 SDK
    try:
        import lark_oapi as lark
        from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1
        from lark_oapi.event.dispatcher_handler import EventDispatcherHandler
        from lark_oapi.core.enum import LogLevel
    except ImportError as e:
        _logger.error("请安装 lark-oapi: pip install lark-oapi (%s)", e)
        raise

    # 同步回调（SDK 要求 sync），内部通过 asyncio.create_task 调度 async 逻辑
    def on_message_receive(event: P2ImMessageReceiveV1) -> None:
        """处理 im.message.receive_v1 事件。"""
        try:
            message = event.event.message
            if not message:
                return

            message_id = message.message_id or ""
            if not message_id:
                _logger.warning("收到无 message_id 的事件，跳过")
                return

            # 去重检查
            if not try_begin_processing(message_id):
                _logger.debug("跳过重复消息: %s", message_id)
                return

            chat_id = message.chat_id or ""
            sender = event.event.sender
            sender_id = (sender.sender_id.open_id or "") if sender and sender.sender_id else ""
            msg_type = message.message_type or ""

            if msg_type != "text":
                release_processing(message_id)
                return

            content_str = message.content or ""
            text = ""
            try:
                parsed = json.loads(content_str)
                text = parsed.get("text", "")
            except (json.JSONDecodeError, TypeError):
                text = content_str

            if not text.strip():
                release_processing(message_id)
                return

            _logger.info("收到消息 [%s] %s: %s", chat_id, sender_id, text)

            # 调度异步处理
            async def _handle():
                try:
                    reply = await message_handler(text, chat_id, sender_id)
                    if reply:
                        await _send_reply(config, chat_id, reply)
                        _logger.debug("已回复 [%s]", chat_id)
                except Exception as e:
                    _logger.error("处理消息失败: %s", e)
                finally:
                    release_processing(message_id)

            enqueue_chat_message(chat_id, _handle)

        except Exception as e:
            _logger.error("事件处理异常: %s", e)

    # 构建 EventDispatcherHandler
    encrypt_key = config.encrypt_key or ""
    verification_token = config.verification_token or ""
    event_handler = (
        EventDispatcherHandler.builder(
            encrypt_key, verification_token
        )
        .register_p2_im_message_receive_v1(on_message_receive)
        .build()
    )

    # 启动 WebSocket 客户端
    try:
        # ── 关键修复：lark-oapi SDK 在模块加载时捕获了 event loop，
        #    但 asyncio.run() 会创建全新 loop。如果不替换，
        #    SDK 的 _receive_message_loop() 会调度到错误的 loop 上，
        #    导致消息永远收不到、思考回调永远不触发。
        import lark_oapi.ws.client as _sdk_ws_mod
        _sdk_ws_mod.loop = asyncio.get_running_loop()

        ws_client = lark.ws.Client(
            app_id=config.app_id,
            app_secret=config.app_secret,
            event_handler=event_handler,
            log_level=LogLevel.INFO,
        )

        _singleton_client = ws_client
        _singleton_app_id = config.app_id

        _logger.info("WebSocket 长轮询模式已启动（无需公网 IP）")
        _logger.info("消息会通过 WebSocket 自动从飞书服务器拉取")

        # lark-oapi 的 start() 是同步方法，内部调用 loop.run_until_complete()
        # 在已运行的事件循环中无法使用。直接调用内部异步方法：
        await ws_client._connect()

        # 启动 ping 循环
        asyncio.create_task(ws_client._ping_loop())

        # 等待连接断开
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            _logger.info("收到退出信号")
            await ws_client._disconnect()

    except Exception as e:
        _logger.error("WebSocket 启动失败: %s", e)
        raise


async def _send_reply(config: FeishuConfig, chat_id: str, reply: str) -> None:
    """通过飞书 API 发送回复（使用交互式卡片）。"""
    try:
        import lark_oapi as lark
        from lark_oapi.api.im.v1 import (
            CreateMessageRequest,
            CreateMessageRequestBody,
        )

        client = lark.Client.builder().app_id(config.app_id).app_secret(config.app_secret).build()

        # 构建飞书交互式卡片
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "🤖 Mini Agent"},
                "template": "blue"
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": reply}}
            ]
        }

        request = CreateMessageRequest.builder() \
            .receive_id_type("chat_id") \
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("interactive")
                .content(json.dumps(card))
                .build()
            ) \
            .build()

        response = client.im.v1.message.create(request)
        if not response.success():
            _logger.warning("发送回复失败: %s %s", response.code, response.msg)

    except ImportError:
        _logger.error("请安装 lark-oapi: pip install lark-oapi")
    except Exception as e:
        _logger.error("发送回复异常: %s", e)
        # 降级为纯文本
        try:
            import lark_oapi as lark
            from lark_oapi.api.im.v1 import (
                CreateMessageRequest,
                CreateMessageRequestBody,
            )
            client = lark.Client.builder().app_id(config.app_id).app_secret(config.app_secret).build()
            request = CreateMessageRequest.builder() \
                .receive_id_type("chat_id") \
                .request_body(
                    CreateMessageRequestBody.builder()
                    .receive_id(chat_id)
                    .msg_type("text")
                    .content(json.dumps({"text": reply}))
                    .build()
                ) \
                .build()
            client.im.v1.message.create(request)
        except Exception:
            pass


async def _send_thinking(config: FeishuConfig, chat_id: str, thinking: str) -> None:
    """通过飞书 API 发送思考过程（使用交互式卡片）。"""
    try:
        import lark_oapi as lark
        from lark_oapi.api.im.v1 import (
            CreateMessageRequest,
            CreateMessageRequestBody,
        )

        client = lark.Client.builder().app_id(config.app_id).app_secret(config.app_secret).build()

        # 截断过长的思考内容
        max_len = 800
        truncated = thinking[:max_len] + "..." if len(thinking) > max_len else thinking

        # 清理可能导致格式问题的字符
        cleaned = truncated.replace("\r", "").replace("\t", "  ")

        # 构建飞书交互式卡片
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "💭 思考中"},
                "template": "gray"
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": cleaned}}
            ]
        }

        request = CreateMessageRequest.builder() \
            .receive_id_type("chat_id") \
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("interactive")
                .content(json.dumps(card, ensure_ascii=False))
                .build()
            ) \
            .build()

        response = client.im.v1.message.create(request)
        if not response.success():
            _logger.warning("发送思考失败: %s %s", response.code, response.msg)

    except ImportError:
        pass  # 静默失败，不影响主流程
    except Exception as e:
        _logger.debug("发送思考异常: %s", e)


__all__ = ["start_feishu_poll_server", "try_begin_processing", "release_processing", "feishu_main", "start_http_bridge", "is_http_bridge_running"]

# ─── CLI 桥接服务器（轻量 HTTP） ───

_BRIDGE_PORT = 18789
_bridge_server = None
_bridge_shared_state = {}


def is_http_bridge_running() -> bool:
    """检查 CLI 桥接服务器是否已在运行。"""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.connect(("127.0.0.1", _BRIDGE_PORT))
            return True
        except (ConnectionRefusedError, OSError):
            return False


class _BridgeHandler(BaseHTTPRequestHandler):
    """轻量 HTTP 桥接：CLI 连接到此端口与飞书实例共享会话。"""

    def do_POST(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            data = json.loads(body)

            action = data.get("action", "")
            state = self.server.shared_state

            if action == "status":
                result = {"status": "ok", "active_sessions": list(state.get("sessions", {}).keys())}
            elif action == "inject":
                session_id = data.get("session_id", "cli-interactive")
                message = data.get("message", "")
                if "sessions" not in state:
                    state["sessions"] = {}
                if session_id not in state["sessions"]:
                    state["sessions"][session_id] = []
                state["sessions"][session_id].append({"role": "user", "content": message, "_injected_by": "cli"})
                result = {"status": "ok", "message": "已注入到会话 " + session_id}
            elif action == "inject_reply":
                session_id = data.get("session_id", "cli-interactive")
                reply = data.get("reply", "")
                if "sessions" not in state:
                    state["sessions"] = {}
                if session_id not in state["sessions"]:
                    state["sessions"][session_id] = []
                state["sessions"][session_id].append({"role": "assistant", "content": reply, "_from": "cli"})
                result = {"status": "ok"}
            elif action == "get_history":
                session_id = data.get("session_id", "cli-interactive")
                history = state.get("sessions", {}).get(session_id, [])
                result = {"status": "ok", "history": history}
            else:
                result = {"status": "error", "message": "未知操作: " + action}

            response = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(response)
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        self.do_POST()

    def log_message(self, format, *args):
        pass  # 静默日志


def start_http_bridge():
    """在后台线程启动轻量 HTTP 桥接服务器，供 CLI 连接。"""
    global _bridge_server
    if _bridge_server:
        return

    server = HTTPServer(("127.0.0.1", _BRIDGE_PORT), _BridgeHandler)
    server.shared_state = _bridge_shared_state
    _bridge_server = server

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _logger.info("HTTP 桥接服务器已启动 (端口 %d)", _BRIDGE_PORT)


def stop_http_bridge():
    """停止 HTTP 桥接服务器。"""
    global _bridge_server
    if _bridge_server:
        _bridge_server.shutdown()
        _bridge_server = None
        _logger.info("HTTP 桥接服务器已停止")


async def feishu_main():
    """飞书独立模式入口（--feishu 参数）。"""
    import signal
    import os
    import asyncio
    from src.feishu.types import FeishuConfig
    from src.feishu.agent_handler import create_feishu_handler
    from src.core.registry import DefaultToolRegistry
    from src.core.monitor import DefaultToolMonitor
    from src.skills.registry import DefaultSkillRegistry
    from src.skills.loader import discover_skill_packages
    from src.tools.filesystem import filesystem_tools
    from src.tools.exec import exec_tools
    from src.tools.web import web_tools
    from src.tools.skills import skills_tools
    from src.tools.self_opt import self_opt_tools
    from pathlib import Path

    registry = DefaultToolRegistry()
    for name, tool in {**filesystem_tools, **exec_tools, **web_tools, **skills_tools, **self_opt_tools}.items():
        registry.register(name, tool)

    monitor = DefaultToolMonitor()
    skill_registry = DefaultSkillRegistry()

    skills_root = os.environ.get("MINI_AGENT_SKILLS", str(Path(__file__).parent.parent / "skills"))
    if os.path.isdir(skills_root):
        packages = await discover_skill_packages(skills_root)
        for pkg in packages:
            skill_registry.register_package(pkg)
            for skill in pkg.skills:
                if skill.tools:
                    for name, tool in skill.tools.items():
                        try:
                            registry.register(name, tool)
                        except ValueError:
                            pass

    skill_toolboxes = skill_registry.get_all_toolboxes()
    skill_prompts = skill_registry.get_system_prompts()

    # 初始化 SessionManager（用于历史持久化）
    from src.session.manager import DefaultSessionManager as SessionManager
    session_manager = SessionManager(registry, skill_toolboxes, [])

    config = FeishuConfig(
        app_id=os.environ.get("FEISHU_APP_ID", ""),
        app_secret=os.environ.get("FEISHU_APP_SECRET", ""),
        verification_token=os.environ.get("FEISHU_VERIFICATION_TOKEN", ""),
    )

    async def _send_thinking_feishu(chat_id: str, thinking: str) -> None:
        """飞书思考回调。"""
        await _send_thinking(config, chat_id, thinking)

    handler = create_feishu_handler(registry, monitor, skill_toolboxes, [], skill_prompts if skill_prompts else None, _send_thinking_feishu, session_manager=session_manager)

    def _on_exit(*_):
        _logger.info("飞书服务已停止")
        # 同步清理子进程
        try:
            from src.core.process_tracker import _sync_cleanup
            _sync_cleanup()
        except Exception:
            pass
        import sys; sys.exit(0)

    signal.signal(signal.SIGINT, _on_exit)
    signal.signal(signal.SIGTERM, _on_exit)

    # 启动 HTTP 桥接服务器（供 CLI 连接）
    start_http_bridge()

    await start_feishu_poll_server(config, handler)
