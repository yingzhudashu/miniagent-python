"""Application lifecycle service for the single Xianyu account."""

from __future__ import annotations

import asyncio
import json
import time

from miniagent.agent.lifecycle import HealthReport, HealthState
from miniagent.assistant.infrastructure.json_config import get_config, get_user_config_path
from miniagent.assistant.infrastructure.paths import resolve_state_dir
from miniagent.assistant.state.sync import immediate_transaction, open_state_database
from miniagent.assistant.xianyu.login import persist_cookie, qr_login
from miniagent.assistant.xianyu.runtime import XianyuRuntime
from miniagent.ui.xianyu.inbound import XianyuInboundProcessor

_PAUSE_STATE_KEY = "xianyu_pause"


class XianyuRuntimeLifecycleService:
    """Configure, activate and persist pause state for one Xianyu runtime."""

    name = "xianyu"

    def __init__(self, *, enabled: bool, runtime: XianyuRuntime, container, state: dict) -> None:
        self.enabled = enabled
        self.runtime = runtime
        self.container = container
        self.state = state
        self.processor: XianyuInboundProcessor | None = None
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        self.processor = XianyuInboundProcessor(self.container, self.state)
        paused, conversations = await asyncio.to_thread(self._load_pause_state)
        if paused:
            self.runtime.pause()
        for conversation_id in conversations:
            self.runtime.pause(conversation_id)
        self._initialized = True

    async def start(self) -> None:
        if self.enabled:
            await self.activate()

    async def activate(self) -> None:
        if not self._initialized:
            await self.initialize()
        cookie = str(get_config("secrets.xianyu_cookie", "") or "").strip()
        if not cookie:
            raise RuntimeError("未配置 secrets.xianyu_cookie；请在 CLI 执行 /xianyu login")
        assert self.processor is not None
        if self.runtime.status().enabled:
            return
        await self.runtime.configure(cookie, self.processor)
        await self.runtime.start()
        self.enabled = True
        self.state["xianyu_enabled"] = True

    async def deactivate(self) -> None:
        await self.runtime.stop()
        self.enabled = False
        self.state["xianyu_enabled"] = False

    async def login(self, status=None) -> None:
        cookie = await qr_login(status=status)
        changed = await asyncio.to_thread(persist_cookie, get_user_config_path(), cookie)
        if changed and self.container.config is not None:
            self.container.config.reload(strict=True)
        if self.runtime.status().enabled:
            await self.runtime.stop()
        await self.activate()

    async def pause(self, conversation_id: str | None = None) -> None:
        self.runtime.pause(conversation_id)
        await asyncio.to_thread(self._save_pause_state)

    async def resume(self, conversation_id: str | None = None) -> None:
        self.runtime.resume(conversation_id)
        await asyncio.to_thread(self._save_pause_state)

    async def stop(self) -> None:
        await self.runtime.stop()
        if self.processor is not None:
            await self.processor.close()
            self.processor = None
        self._initialized = False

    def health(self) -> HealthReport:
        if not self.enabled:
            return HealthReport(HealthState.STOPPED, "disabled")
        return self.runtime.health()

    def _load_pause_state(self) -> tuple[bool, set[str]]:
        with open_state_database(resolve_state_dir()) as connection:
            row = connection.execute(
                "SELECT value_json FROM cli_state WHERE state_key=?", (_PAUSE_STATE_KEY,)
            ).fetchone()
        if row is None:
            return False, set()
        try:
            value = json.loads(str(row[0]))
        except json.JSONDecodeError:
            return False, set()
        if not isinstance(value, dict):
            return False, set()
        conversations = value.get("conversations") or []
        return bool(value.get("global")), {str(item) for item in conversations if str(item).strip()}

    def _save_pause_state(self) -> None:
        payload = json.dumps(
            {
                "global": self.runtime._paused,
                "conversations": sorted(self.runtime._paused_conversations),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with open_state_database(resolve_state_dir()) as connection:
            with immediate_transaction(connection):
                connection.execute(
                    """INSERT INTO cli_state VALUES (?, ?, ?)
                       ON CONFLICT(state_key) DO UPDATE SET value_json=excluded.value_json,
                         updated_at_ms=excluded.updated_at_ms""",
                    (_PAUSE_STATE_KEY, payload, int(time.time() * 1000)),
                )


__all__ = ["XianyuRuntimeLifecycleService"]
