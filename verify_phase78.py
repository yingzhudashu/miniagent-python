"""Phase 7-8 Verification Script

Verifies CLI entry and Feishu integration modules.
Uses ASCII-only output for Windows console compatibility.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile


def check(label: str, condition: bool) -> None:
    status = "[OK]" if condition else "[FAIL]"
    print(f"  {status} {label}")
    if not condition:
        raise AssertionError(f"FAILED: {label}")


async def main() -> None:
    errors = 0

    # ================================================================
    # Phase 7: CLI Entry
    # ================================================================
    print("\nPhase 7: CLI Entry")

    # 7.1 CLI Module
    print("\n  --- CLI Module ---")
    try:
        from src.cli.cli import main as cli_main
        from src.cli.cli import (
            registry,
            monitor,
            skill_registry,
            load_skills,
            print_welcome,
        )
        check("cli_main importable", True)
        check("registry global exists", registry is not None)
        check("monitor global exists", monitor is not None)
        check("skill_registry global exists", skill_registry is not None)
        check("load_skills callable", callable(load_skills))
        check("print_welcome callable", callable(print_welcome))
    except Exception as e:
        print(f"  [FAIL] CLI Module error: {e}")
        errors += 1

    # 7.2 __main__ entry
    print("\n  --- __main__ ---")
    try:
        from src.__main__ import main as entry_main
        check("__main__.main importable", True)
        check("__main__.main callable", callable(entry_main))
    except Exception as e:
        print(f"  [FAIL] __main__ error: {e}")
        errors += 1

    # 7.3 InstanceManager convenience functions
    print("\n  --- InstanceManager ---")
    try:
        from src.core.instance_manager import (
            InstanceManager,
            try_acquire_instance,
            force_acquire_instance,
            release_instance,
            stop_instance,
        )
        check("InstanceManager importable", True)
        check("try_acquire_instance callable", callable(try_acquire_instance))
        check("force_acquire_instance callable", callable(force_acquire_instance))
        check("release_instance callable", callable(release_instance))
        check("stop_instance callable", callable(stop_instance))

        # Functional test
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = InstanceManager(state_dir=tmpdir)
            result = mgr.try_acquire()
            check("try_acquire success", result.get("success") is True)

            result2 = mgr.try_acquire()
            check("second try_acquire fails", result2.get("success") is False)
            check("returns existing_pid", "existing_pid" in result2)

            mgr.release()
            check("release no exception", True)

            mgr2 = InstanceManager(state_dir=tmpdir)
            result3 = mgr2.try_acquire()
            check("try_acquire succeeds after release", result3.get("success") is True)

            # Don't call stop() on ourselves - it would kill the test process
            # Instead test stop() when no instance is running
            mgr3 = InstanceManager(state_dir=tmpdir)
            # Clean up the pid file first
            if os.path.exists(mgr3._pid_file):
                os.unlink(mgr3._pid_file)
            result_stop = mgr3.stop()
            check("stop fails gracefully when no instance", result_stop.get("success") is False)

    except Exception as e:
        print(f"  [FAIL] InstanceManager error: {e}")
        errors += 1

    # ================================================================
    # Phase 8: Feishu Integration
    # ================================================================
    print("\nPhase 8: Feishu Integration")

    # 8.1 Feishu Types
    print("\n  --- Feishu Types ---")
    try:
        from src.feishu.types import FeishuConfig, FeishuMessageEvent, FeishuReply

        config = FeishuConfig(app_id="test_app", app_secret="test_secret", port=8080)
        check("FeishuConfig created", config.app_id == "test_app")
        check("FeishuConfig default port=0", FeishuConfig(app_id="x", app_secret="y").port == 0)

        event = FeishuMessageEvent(
            message_id="msg_123",
            chat_id="chat_456",
            sender_id="sender_789",
            msg_type="text",
            content="Hello",
        )
        check("FeishuMessageEvent created", event.message_id == "msg_123")

        reply = FeishuReply(content="Hi there")
        check("FeishuReply default msg_type=text", reply.msg_type == "text")
        check("FeishuReply default receive_id_type=chat_id", reply.receive_id_type == "chat_id")

    except Exception as e:
        print(f"  [FAIL] Feishu Types error: {e}")
        errors += 1

    # 8.2 Poll Server
    print("\n  --- Poll Server ---")
    try:
        from src.feishu.poll_server import (
            start_feishu_poll_server,
            try_begin_processing,
            release_processing,
        )

        check("start_feishu_poll_server callable", callable(start_feishu_poll_server))
        check("try_begin_processing callable", callable(try_begin_processing))
        check("release_processing callable", callable(release_processing))

        # Dedup functional test
        msg_id = f"test_msg_{id(object())}"
        check("first processing allowed", try_begin_processing(msg_id) is True)
        check("duplicate processing denied", try_begin_processing(msg_id) is False)
        release_processing(msg_id)
        # After release, key is stored in disk dedup so it's still denied (correct behavior)
        check("still denied after release (disk dedup)", try_begin_processing(msg_id) is False)

    except Exception as e:
        print(f"  [FAIL] Poll Server error: {e}")
        errors += 1

    # 8.3 Webhook Server
    print("\n  --- Webhook Server ---")
    try:
        from src.feishu.server import create_feishu_server, start_feishu_server

        check("create_feishu_server callable", callable(create_feishu_server))
        check("start_feishu_server callable", callable(start_feishu_server))

        config = FeishuConfig(app_id="test", app_secret="test", port=0)

        async def dummy_handler(text, chat_id, sender_id):
            return f"echo: {text}"

        server = create_feishu_server(config, dummy_handler)
        check("server instance created", server is not None)

    except Exception as e:
        print(f"  [FAIL] Webhook Server error: {e}")
        errors += 1

    # 8.4 Agent Handler
    print("\n  --- Agent Handler ---")
    try:
        from src.feishu.agent_handler import create_feishu_handler
        from src.core.registry import DefaultToolRegistry
        from src.core.monitor import DefaultToolMonitor

        handler = create_feishu_handler(
            registry=DefaultToolRegistry(),
            monitor=DefaultToolMonitor(),
            toolboxes=[],
            skills=[],
            skill_prompts=None,
        )
        check("create_feishu_handler returns callable", callable(handler))

        reply = await handler("test message", "test_chat", "test_sender")
        check("handler returns string reply", isinstance(reply, str))

    except Exception as e:
        print(f"  [FAIL] Agent Handler error: {e}")
        errors += 1

    # 8.5 Feishu __init__
    print("\n  --- Feishu __init__ ---")
    try:
        from src.feishu import (
            FeishuConfig,
            start_feishu_poll_server,
            create_feishu_server,
            create_feishu_handler,
        )
        check("all public APIs importable from __init__", True)
    except Exception as e:
        print(f"  [FAIL] Feishu __init__ error: {e}")
        errors += 1

    # ================================================================
    # Summary
    # ================================================================
    print("\n" + "=" * 50)
    if errors == 0:
        print("SUCCESS: Phase 7-8 all verified!")
        print("  Phase 7: CLI entry + InstanceManager [OK]")
        print("  Phase 8: Feishu Types + Poll Server + Webhook Server + Agent Handler [OK]")
    else:
        print(f"FAIL: {errors} modules failed verification")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
