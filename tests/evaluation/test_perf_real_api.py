"""真实API性能测试。

要求：
- 使用真实LLM API（OpenAI/Claude）
- 测量真实延迟、token usage
- 验证错误重试机制
- 记录性能基准

注意：
- 需配置API Key（config.user.json）
- 不在默认CI运行；必须显式设置 MINIAGENT_REAL_API_STRESS=1
- 结果默认写入 workspaces/logs/perf/，避免提交过程性压测产物
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path

import pytest
import pytest_asyncio

from miniagent.agent.executor import execute_plan
from miniagent.agent.observability import (
    TraceRuntimeConfig,
    auto_register_trace_file_hook,
    emit_trace,
    shutdown_trace_writer,
)
from miniagent.assistant.infrastructure.json_config import get_config
from miniagent.llm.factory import create_llm_gateway


@pytest.fixture
def real_api_config():
    """验证API配置存在并加载到环境变量。"""
    from miniagent.assistant.infrastructure.env_loader import load_secrets_from_project_root

    if os.environ.get("MINIAGENT_REAL_API_STRESS") != "1":
        pytest.skip("真实 API 压测需显式设置 MINIAGENT_REAL_API_STRESS=1")

    # 加载secrets到环境变量
    load_secrets_from_project_root()

    from miniagent.assistant.infrastructure.json_config import (
        JsonConfigLoader,
        install_config_loader,
    )

    trace_root = Path(
        os.environ.get("MINIAGENT_REAL_API_PERF_DIR", "workspaces/logs/perf")
    ) / f"evaluation-trace-pid{os.getpid()}"
    loader = JsonConfigLoader()
    loader.reload(strict=True)
    install_config_loader(
        loader.with_runtime_overrides(
            {
                "trace": {
                    "enabled": True,
                    "output_dir": str(trace_root),
                    "record_payload": "metrics_only",
                    "resource_sample_interval_seconds": 0.25,
                    "auto_cleanup": False,
                }
            }
        )
    )
    auto_register_trace_file_hook(TraceRuntimeConfig.from_getter(get_config))

    # 返回完整配置供测试使用
    return {
        "model": get_config("llm.models.primary", {}),
        "agent": get_config("agent", {}),
    }


@pytest_asyncio.fixture
async def real_api_client(real_api_config):
    """Own one provider-neutral gateway per test and close it afterward."""
    try:
        client = create_llm_gateway(get_config)
    except RuntimeError as error:
        pytest.skip(f"所选 provider 凭据不可用: {type(error).__name__}")
    try:
        yield client
    finally:
        await client.close()


@pytest.fixture
def baseline_dir():
    """真实 API 压测输出目录。"""
    path = Path(os.environ.get("MINIAGENT_REAL_API_PERF_DIR", "workspaces/logs/perf"))
    if os.environ.get("MINIAGENT_REAL_API_STRESS") == "1":
        path.mkdir(parents=True, exist_ok=True)
    return path


class TestRealAPIPerformance:
    """真实API性能测试套件。"""

    @pytest.mark.asyncio
    async def test_llm_streaming_latency(
        self,
        real_api_config,
        real_api_client,
        baseline_dir,
        memory_runtime,
        knowledge_registry,
    ):
        """测量真实LLM流式响应延迟。"""
        from miniagent.agent.monitor import DefaultToolMonitor
        from miniagent.agent.tools.registry import DefaultToolRegistry
        from miniagent.agent.types.config import AgentConfig  # 修复导入路径
        from miniagent.agent.types.planning import StructuredPlan

        # 准备测试输入
        user_input = "请分析以下代码的性能瓶颈：def slow_func(n): return sum(range(n))"

        # 创建必要的对象
        registry = DefaultToolRegistry()
        from miniagent.assistant.engine.builtin_tools import register_builtin_tools

        register_builtin_tools(registry)
        monitor = DefaultToolMonitor()
        agent_config = AgentConfig(
            max_turns=1,  # 只执行1轮
            tool_timeout=60,
        )

        # 创建简单计划（无工具调用）
        plan = StructuredPlan(
            summary="纯LLM测试",
            steps=[],
            required_toolboxes=[],
        )

        # 测量流式响应延迟
        start = time.perf_counter()
        try:
            result = await execute_plan(
                plan=plan,
                user_input=user_input,
                registry=registry,
                monitor=monitor,
                agent_config=agent_config,
                memory=memory_runtime,
                knowledge_registry=knowledge_registry,
                client=real_api_client,
            )
            elapsed = time.perf_counter() - start

            # 记录性能数据
            perf_data = {
                "test": "llm_streaming_latency",
                "elapsed_ms": elapsed * 1000,
                "timestamp": datetime.now().isoformat(),
                "success": True,
            }

            # 验证延迟在合理范围（网络环境不同，放宽到60秒）
            assert elapsed < 60.0, f"LLM响应过慢: {elapsed}s"
            assert result and len(result) > 0, "应有返回结果"

            # 写入基线文件
            baseline_file = baseline_dir / "real-api-test-results.json"
            baseline_dir.mkdir(exist_ok=True, parents=True)
            if baseline_file.exists():
                with baseline_file.open(encoding="utf-8") as f:
                    baseline = json.load(f)
                baseline.setdefault("tests", []).append(perf_data)
            else:
                baseline = {"schema_version": 1, "tests": [perf_data]}

            with baseline_file.open("w", encoding="utf-8") as f:
                json.dump(baseline, f, ensure_ascii=False, indent=2)

        except Exception as e:
            elapsed = time.perf_counter() - start
            pytest.fail(f"LLM调用失败: {e}（耗时 {elapsed}s）")

    @pytest.mark.asyncio
    async def test_tool_execution_with_real_api(
        self,
        real_api_config,
        real_api_client,
        memory_runtime,
        knowledge_registry,
    ):
        """测量真实工具执行延迟（带LLM）。"""
        from miniagent.agent.monitor import DefaultToolMonitor
        from miniagent.agent.tools.registry import DefaultToolRegistry
        from miniagent.agent.types.config import AgentConfig  # 修复导入路径
        from miniagent.agent.types.planning import StructuredPlan

        # 准备测试输入（触发工具调用）
        user_input = "请读取README.md文件的前10行内容"

        # 创建必要的对象
        registry = DefaultToolRegistry()
        from miniagent.assistant.engine.builtin_tools import register_builtin_tools

        register_builtin_tools(registry)
        monitor = DefaultToolMonitor()
        agent_config = AgentConfig(
            max_turns=2,  # 允许工具调用
            tool_timeout=60,
        )

        # 创建带工具的计划
        plan = StructuredPlan(
            summary="工具执行测试",
            steps=[],
            required_toolboxes=["file_read"],
        )

        # 测量总延迟（LLM + 工具）
        start = time.perf_counter()
        try:
            result = await execute_plan(
                plan=plan,
                user_input=user_input,
                registry=registry,
                monitor=monitor,
                agent_config=agent_config,
                memory=memory_runtime,
                knowledge_registry=knowledge_registry,
                client=real_api_client,
            )
            elapsed = time.perf_counter() - start

            # 记录到trace系统
            emit_trace({
                "type": "perf.tool_execution_real_api",
                "duration_ms": elapsed * 1000,
                "success": True,
            })

            # 验证延迟合理（工具调用 <60秒，网络环境差异大）
            assert elapsed < 60.0, f"工具执行过慢: {elapsed}s"
            assert result and len(result) > 0, "应有返回结果"
            read_stats = monitor.get_stats("read_file")
            assert read_stats is not None and read_stats.success_count >= 1, (
                "真实工具场景必须成功调用 read_file"
            )

        except Exception as e:
            elapsed = time.perf_counter() - start
            emit_trace({
                "type": "perf.tool_execution_real_api",
                "duration_ms": elapsed * 1000,
                "success": False,
                "error": str(e),
            })
            pytest.fail(f"工具执行失败: {e}")

    @pytest.mark.asyncio
    async def test_concurrent_requests_throughput(
        self,
        real_api_config,
        real_api_client,
        baseline_dir,
        memory_runtime,
        knowledge_registry,
    ):
        """测量并发请求吞吐量。"""
        from miniagent.agent.monitor import DefaultToolMonitor
        from miniagent.agent.tools.registry import DefaultToolRegistry
        from miniagent.agent.types.config import AgentConfig  # 修复导入路径
        from miniagent.agent.types.planning import StructuredPlan

        # 并发发送3个请求（避免API限流）
        num_requests = 3

        # 创建共享对象
        registry = DefaultToolRegistry()
        from miniagent.assistant.engine.builtin_tools import register_builtin_tools

        register_builtin_tools(registry)
        monitor = DefaultToolMonitor()
        agent_config = AgentConfig(max_turns=1, tool_timeout=60)

        # 创建并发任务
        tasks = []
        for i in range(num_requests):
            plan = StructuredPlan(
                summary=f"并发测试{i}",
                steps=[],
                required_toolboxes=[],
            )
            tasks.append(
                execute_plan(
                    plan=plan,
                    user_input=f"测试请求{i}: 请简单介绍一下Python",
                    registry=registry,
                    monitor=monitor,
                    agent_config=agent_config,
                    memory=memory_runtime,
                    knowledge_registry=knowledge_registry,
                    client=real_api_client,
                )
            )

        start = time.perf_counter()
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            elapsed = time.perf_counter() - start

            # 计算吞吐量（请求/秒）
            success_count = sum(1 for r in results if not isinstance(r, Exception))
            throughput = success_count / elapsed

            # 记录性能数据
            perf_data = {
                "test": "concurrent_throughput",
                "num_requests": num_requests,
                "success_count": success_count,
                "elapsed_s": elapsed,
                "throughput_req_per_s": throughput,
                "timestamp": datetime.now().isoformat(),
            }

            # 写入基线文件
            baseline_file = baseline_dir / "concurrent-test-results.json"
            baseline_dir.mkdir(exist_ok=True, parents=True)
            with baseline_file.open("w", encoding="utf-8") as f:
                json.dump(perf_data, f, ensure_ascii=False, indent=2)

            # 验证吞吐量合理（>0.05 req/s，网络环境差异大，给API波动留空间）
            # 注意：真实API测试受网络、API响应速度影响，0.09 req/s已经接近阈值
            assert throughput > 0.05, f"并发吞吐量过低: {throughput} req/s"
            assert success_count == num_requests, (
                f"并发请求必须全部成功: {success_count}/{num_requests}"
            )
            assert all(isinstance(result, str) and result for result in results)

        except Exception as e:
            pytest.fail(f"并发测试失败: {e}")

    def test_api_configuration_valid(self, real_api_config):
        """验证API配置有效。"""
        # 验证模型配置
        llm_overrides = real_api_config.get("model", {})
        assert llm_overrides.get("provider"), "应配置模型 provider"
        assert llm_overrides.get("model"), "应配置模型名称"

        # 记录配置信息（不含密钥）
        emit_trace({
            "type": "perf.api_config_valid",
            "model": llm_overrides.get("model", "unknown"),
            "base_url": llm_overrides.get("base_url", "unknown")[:50] if llm_overrides.get("base_url") else "default",
        })


def test_baseline_files_exist(baseline_dir):
    """验证性能基线目录存在。"""
    if os.environ.get("MINIAGENT_REAL_API_STRESS") != "1":
        pytest.skip("真实 API 压测需显式设置 MINIAGENT_REAL_API_STRESS=1")
    assert baseline_dir.exists(), "性能基线目录不存在"
    assert baseline_dir.is_dir(), "性能基线目录不是目录"


def cleanup_trace_writer():
    """测试后清理trace写入器。"""
    try:
        shutdown_trace_writer()
    except Exception:
        pass


# pytest cleanup hook
@pytest.fixture(autouse=True)
def cleanup_after_test():
    """每个测试后自动清理。"""
    yield
    cleanup_trace_writer()
