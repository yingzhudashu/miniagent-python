# MiniAgent 性能优化任务清单

## 执行日期：2026-06-03
## 已完成优化：Phase 1 - 基础设施 + 核心正则预编译

---

## 后续优化任务（按优先级排序）

### 📋 任务执行方法

每个任务包含：
- **文件路径**：具体文件位置
- **优化位置**：行号范围
- **问题描述**：具体性能问题
- **优化方法**：详细修改步骤
- **预期效果**：性能提升估计
- **验证方法**：测试命令

---

## 🔴 高优先级任务（立即执行）

### 任务1：transcript内存优化

**文件**：`miniagent/engine/main.py`
**位置**：373-414, 1331-1360
**问题**：
- `_MAX_TRANSCRIPT_CHARS = 400_000` 过大
- `_trim_transcript()` 每次遍历整个列表（O(n)）

**优化方法**：
```python
# 1. 降低上限
_MAX_TRANSCRIPT_CHARS = 200_000

# 2. 维护累计长度计数器
_transcript_total_len: list[int] = [0]

def _trim_transcript() -> None:
    global _transcript_total_len
    while _transcript_total_len[0] > _MAX_TRANSCRIPT_CHARS and len(_transcript) > 16:
        old = _transcript.pop(0)
        _transcript_total_len[0] -= _transcript_fragment_len(old)
```

**预期效果**：内存减少50%，trim速度提升10倍

---

### 任务2：流式输出缓冲优化

**文件**：`miniagent/core/executor.py`
**位置**：568-610（_StreamingBuffer类）

**问题**：
- 每100个chunk合并一次，仍可能累积大量chunk
- getvalue()需要拼接所有chunk

**优化方法**：
```python
# 降低合并阈值（从100改为50）
if len(self._chunks) > 50:
    self._consolidated = "".join(self._chunks)
    self._chunks = [self._consolidated]

# 简化getvalue逻辑
def getvalue(self) -> str:
    if self._consolidated:
        if len(self._chunks) == 1:
            return self._consolidated
        return self._consolidated + "".join(self._chunks[1:])
    return "".join(self._chunks)
```

**预期效果**：内存占用降低，字符串拼接速度提升

---

### 任务3：JSON解析缓存

**文件**：`miniagent/core/executor.py`
**位置**：109-119, 748-757, 860-870

**问题**：工具参数可能重复解析json.loads()

**优化方法**：
```python
# 第一次解析后缓存到对象属性（已有）
args = getattr(tc, "_args_dict", None) or json.loads(tc.function.arguments)

# 或使用perf_cache模块
from miniagent.infrastructure.perf_cache import cached_json_serialize
```

**预期效果**：避免重复解析，CPU减少约5%

---

### 任务4：剩余高频正则预编译

**文件列表**（按调用频率排序）：

| 文件 | 行号 | 正则 | 优先级 |
|------|------|------|--------|
| `miniagent/engine/cli_commands.py` | 243 | 质量评估建议提取 | 高 |
| `miniagent/feishu/poll_server.py` | 多处 | Markdown转换 | 高 |
| `miniagent/engine/markdown_cli.py` | 多处 | 标题检测 | 高 |
| `miniagent/memory/history_progressive.py` | 72-183 | 历史处理 | 中 |
| `miniagent/skills/loader.py` | 63-296 | 技能加载 | 中 |
| `miniagent/skills/clawhub_client.py` | 259-265 | frontmatter | 中 |

**统一优化方法**：
```python
# 模块顶部预编译
_PATTERN_NAME = re.compile(r"pattern_string")

# 函数内使用
matches = _PATTERN_NAME.findall(text)
```

---

## 🟡 中优先级任务（短期执行）

### 任务5：终端宽度缓存TTL增加

**文件**：`miniagent/engine/thinking.py`
**位置**：59-72

**优化方法**：
```python
_TERMINAL_WIDTH_CACHE_TTL: float = 5.0  # 从2秒增加到5秒
```

**预期效果**：减少终端宽度查询频率

---

### 任务6：Markdown渲染缓存扩大

**文件**：`miniagent/engine/markdown_cli.py`
**位置**：95-180

**优化方法**：
```python
_RENDER_CACHE_MAX_SIZE = 200  # 从100增加到200
```

---

### 任务7：Session状态LRU驱逐

**文件**：`miniagent/engine/thinking.py`
**位置**：238-249

**优化方法**：
```python
_MAX_SESSION_STATES = 50
while len(self._states) > _MAX_SESSION_STATES:
    oldest_key = next(iter(self._states))
    self._states.pop(oldest_key)
```

---

### 任务8：文件IO异步化

**文件**：`miniagent/engine/main.py`
**位置**：334-366（历史加载）

**优化方法**：
```python
# 使用异步包装
messages = await asyncio.to_thread(json.load, f)

# 或延迟加载
asyncio.create_task(_load_initial_history_to_transcript())
```

---

## 🟢 低优先级任务（长期优化）

### 任务9：字符串操作统一优化

**涉及文件**：
- `miniagent/engine/engine.py` (line 586)
- `miniagent/feishu/poll_server.py`
- 其他字符串密集操作文件

**统一方法**：使用`OptimizedStringBuilder`（见perf_cache.py）

---

### 任务10：锁竞争优化（架构重构）

**文件**：`miniagent/infrastructure/message_queue.py`

**注意**：此任务需要架构重构，建议单独规划

---

## 📊 性能测试计划

### 基准测试命令

```bash
# 内存测试
python -c "
from miniagent.infrastructure.perf_cache import get_memory_usage_mb
print(f'Memory: {get_memory_usage_mb():.2f} MB')
"

# 性能对比测试
python -m pytest tests/test_performance.py -v --benchmark

# 正则缓存统计
python -c "
from miniagent.infrastructure.perf_cache import _regex_cache
print(f'Regex cache size: {len(_regex_cache)}')
"
```

---

## 📝 执行日志模板

每完成一个任务，记录：

```
### 任务X完成 - YYYY-MM-DD HH:MM

- 文件：xxx.py
- 修改：详细描述
- 测试：pytest结果
- 效果：实际性能提升
- 问题：遇到的困难
```

---

## 🎯 预期总体效果

完成所有优化后：
- **内存占用**：降低约30-50%
- **CPU消耗**：降低约10-20%
- **响应速度**：提升约15-30%
- **稳定性**：避免内存泄漏风险

---

## 📞 技术支持

遇到问题可参考：
- `miniagent/infrastructure/perf_cache.py`（优化工具类）
- `PERFORMANCE_OPTIMIZATION_PLAN.md`（总体计划）
- pytest测试报告

---

**执行建议**：
1. 每次只执行1-2个任务
2. 每个任务完成后立即测试
3. 记录执行日志
4. 出现问题立即回滚

**时间估算**：
- 高优先级任务：约2-4小时
- 中优先级任务：约3-6小时
- 低优先级任务：约5-10小时

总计：约10-20小时工作量