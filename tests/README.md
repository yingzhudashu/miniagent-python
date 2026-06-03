# MiniAgent 测试指南

## 测试结构

```
tests/
├── conftest.py          # 测试配置与 fixtures
├── test_*.py            # 核心模块测试
├── evaluation/          # 离线评测测试
│   ├── conftest.py      # 自动标记 evaluation
│   └── samples/         # 评测样本数据
└── performance/         # 性能基准测试
    ├── benchmarks.py    # 性能场景
    └── perf_helpers_*.py
```

## 运行测试

### 快速测试（排除 evaluation）
```bash
pytest tests/ -q -m "not evaluation"
```

### 完整测试
```bash
pytest tests/ -q
```

### 仅单元测试
```bash
pytest tests/ -m unit
```

### 仅集成测试
```bash
pytest tests/ -m integration
```

### 仅 evaluation 测试
```bash
pytest tests/ -m evaluation -v
```

### 性能测试
```bash
pytest tests/ -m perf -v
```

### 排除慢测试
```bash
pytest tests/ -q -m "not slow"
```

## 覆盖率报告

### 生成 HTML 报告
```bash
pytest tests/ -q -m "not evaluation" \
  --cov=miniagent --cov-report=html --cov-report=term
```

### 打开报告
- Windows: `start htmlcov/index.html`
- macOS: `open htmlcov/index.html`
- Linux: `xdg-open htmlcov/index.html`

### 覆盖率目标
- 核心模块: 95%+
- 类型定义: 95%+
- 整体项目: 80%+

## 编写测试

### 测试命名
- 文件: `test_<模块名>.py`
- 函数: `test_<功能描述>`
- 类: `Test<功能类别>`

### 测试分类
使用 pytest markers:
- `@pytest.mark.unit` - 单元测试
- `@pytest.mark.integration` - 集成测试
- `@pytest.mark.functional` - 功能测试
- `@pytest.mark.evaluation` - 离线评测
- `@pytest.mark.perf` - 性能测试
- `@pytest.mark.slow` - 慢测试（>5秒）
- `@pytest.mark.asyncio` - 异步测试

### 测试示例
```python
import pytest
from miniagent.types.tool import ToolContext, ToolResult

class TestToolContext:
    """测试工具执行上下文"""

    def test_context_defaults(self) -> None:
        """默认值验证"""
        ctx = ToolContext(cwd="/tmp")
        assert ctx.permission == "sandbox"
        assert ctx.allowed_paths == []

    def test_context_all_fields(self) -> None:
        """所有字段"""
        ctx = ToolContext(
            cwd="/test",
            permission="allowlist",
            session_key="session-123",
        )
        assert ctx.cwd == "/test"
```

### Fixtures
项目提供以下 fixtures:
- `state_dir` - 临时状态目录
- `_reset_process_singletons_after_test` - 自动重置单例

### 最佳实践
1. 每个测试一个清晰的断言目的
2. 使用 fixtures 共享测试数据
3. Mock 外部依赖（网络、数据库）
4. 异步测试使用 `@pytest.mark.asyncio`
5. 清理测试创建的临时资源

## 测试统计

运行 `pytest tests/ --collect-only -q` 获取当前测试数量。

截至 2026-06-03:
- 约 900 个测试
- 约 130 个测试文件
- 约 46 个测试类