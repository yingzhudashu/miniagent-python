"""评测子目录共用 fixture / 收集钩子。

默认 CI 使用 ``pytest -m "not evaluation"`` 排除本目录下用例（可能依赖网络或真实 API Key）。
新增 ``tests/evaluation/test_*.py`` 时无需逐文件打标：收集阶段会自动附加 ``evaluation`` marker。
"""

from __future__ import annotations

from pathlib import Path

import pytest


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """将路径位于 ``tests/evaluation/`` 下的用例统一打上 ``evaluation`` marker。"""
    mark = pytest.mark.evaluation
    for item in items:
        path = getattr(item, "path", None)
        if path is None:
            path = Path(str(getattr(item, "fspath", "")))
        else:
            path = Path(path)
        try:
            rel = path.resolve().relative_to(Path(__file__).resolve().parent)
        except ValueError:
            continue
        # 仅标记本目录及其子目录中的用例文件（不含误标其它树）
        if rel.parts:
            item.add_marker(mark)
