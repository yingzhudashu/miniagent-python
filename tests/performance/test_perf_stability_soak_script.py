"""Smoke contract for the local stability soak."""

from __future__ import annotations

import pytest
from scripts.perf_stability_soak import run_soak


@pytest.mark.asyncio
async def test_short_stability_soak_closes_resources_without_trace_loss() -> None:
    result = await run_soak(duration_seconds=0.15, interval_seconds=0.01)

    assert result["iterations"] > 0
    assert result["writer"]["emitted_count"] == result["writer"]["written_count"]
    assert result["validation_errors"] == []
