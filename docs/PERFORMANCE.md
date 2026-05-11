# 性能测试与优化

> Mini Agent Python | 与 `miniagent.__version__` 对齐 | 补充 [ENGINEERING.md](ENGINEERING.md)

本文说明如何度量 **进程内** 开销（CPU、内存、本地 I/O），如何与 **端到端（含 LLM 网络）** 指标区分，以及如何维护基线与可选 CI。

## 1. KPI 分层

| 层级 | 指标 | 说明 |
|------|------|------|
| **L1 本地** | `wall_local`（秒）、`alloc_delta`（字节，可选 tracemalloc）、**关键词索引写盘次数**、上下文 `json.dumps` 相关耗时 | 使用 **Mock LLM**，不访问外网；见 `tests/test_perf_synthetic.py` |
| **L2 剖析** | cProfile 累计时间、py-spy 火焰图、RSS | 开发机按需运行，见 §3 |
| **L3 端到端** | 单用例 wall time、token usage | 依赖 API，见 `tests/evaluation/`、[EVALUATION_LOCAL.md](EVALUATION_LOCAL.md)；飞书路径另计 PATCH/轮询 |

**注意**：线上感知的 p95 延迟通常由 **LLM/HTTP** 主导；L1 回归用于防止 Python 侧退化，不替代端到端评测。

## 2. 场景矩阵（合成）

| ID | 场景 | 目的 |
|----|------|------|
| S1 | `execute_plan` + Mock 流式、1 次工具调用 | 执行器主路径本地耗时上界（宽松断言） |
| S2 | `DefaultMemoryStore.add_entry` 多次 + 单次 `flush_keyword_index` | 关键词索引 **合并落盘**（相对「每 add 一次 save」） |
| S3 | `DefaultContextManager` + 较多工具 schema 的 token 估算 | 上下文/序列化路径冒烟（阈值宽松） |

扩展场景时保持 **确定性**（固定 tmp 状态目录、Mock client），避免在默认 CI 中依赖网络。

## 3. 本地剖析命令

### 3.1 cProfile（CPU）

```bash
python -m cProfile -o perf.out scripts/perf_profile_tracemalloc.py --no-tracemalloc
python -c "import pstats; p=pstats.Stats('perf.out'); p.strip_dirs().sort_stats('cumtime').print_stats(40)"
```

### 3.2 tracemalloc（分配热点）

```bash
python scripts/perf_profile_tracemalloc.py --top 25
python scripts/perf_profile_tracemalloc.py --json-out perf-snapshot.json
```

### 3.3 py-spy（采样，需单独安装）

```bash
py-spy record -o profile.svg -- python scripts/perf_profile_tracemalloc.py --no-tracemalloc
```

## 4. 基线文件格式（`tests/perf_baselines/`）

用于人工或离线对比（**勿提交密钥**）。示例见 [tests/perf_baselines/example.json](../tests/perf_baselines/example.json)。

字段建议：

- `schema_version`：整数，格式变更时递增  
- `commit`：可选，Git SHA  
- `generated_at`：ISO8601 UTC  
- `environment`：`python`, `platform`  
- `scenarios`：数组，元素含 `id`, `median_ms`, `notes`  

CI **不**依赖基线文件是否存在；可选 workflow 仅上传当次脚本输出 artifact。

## 5. 已确认/已缓解的热点（代码侧）

- **关键词索引**：`KeywordIndex` 使用 `_dirty`；`index_entry` 只改内存。`DefaultMemoryStore.add_entry` 不再每次 `save()`；在 [`miniagent/core/executor.py`](../miniagent/core/executor.py) 的 `_save_session_memory` 末尾 **`flush_keyword_index()`** 保证每轮仍落盘一次。批量 `add_entry` 后显式 `flush_keyword_index()` 可减少写次数。进程退出时 [`miniagent/memory/defaults.py`](../miniagent/memory/defaults.py) 通过 **atexit** 再刷一次默认 bundle 的索引，降低异常退出丢失概率。  
- **Feishu `json.dumps`**：仍可能是热点；优化前应用 py-spy 对 `poll_server` 路径采样确认。

## 6. 相关文件

| 文件 | 作用 |
|------|------|
| [`tests/perf_helpers.py`](../tests/perf_helpers.py) | 中位数计时、可选 tracemalloc |
| [`tests/test_perf_synthetic.py`](../tests/test_perf_synthetic.py) | 合成 perf 用例（默认参与 `pytest -m "not evaluation"`） |
| [`scripts/perf_profile_tracemalloc.py`](../scripts/perf_profile_tracemalloc.py) | 本地可重复剖析入口 |
