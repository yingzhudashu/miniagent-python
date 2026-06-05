# 真实API性能测试配置指南

## 1. 配置API Key

复制`config.defaults.json`为`config.user.json`并填写：

```json
{
  "llm": {
    "provider": "openai",
    "api_key": "your-real-api-key",
    "model": "gpt-4-turbo"
  }
}
```

## 2. 运行真实API测试

```bash
# 运行真实API性能测试（单独触发）
pytest tests/evaluation/test_perf_real_api.py -v -s

# 生成性能报告
python scripts/perf_profile_tracemalloc.py --inner-repeat 10 --json-out real-api-snapshot.json
```

## 3. 性能指标测量

- **延迟**：LLM响应时间、工具执行时间
- **Token usage**：prompt/completion token统计
- **内存**：tracemalloc峰值测量
- **CPU**：使用py-spy采样

## 4. 基准对比

```bash
# 对比两次测试结果
python scripts/compare_perf_snapshots.py \
    tests/perf_baselines/real-api-baseline.json \
    real-api-snapshot.json
```

## 5. 注意事项

- 真实API测试需要网络连接和有效API密钥
- 测试可能产生API调用费用
- 不在默认CI运行（需单独触发）
- 测试结果写入`tests/perf_baselines/`供后续对比

## 6. 性能基线目录结构

```
tests/perf_baselines/
├── real-api-baseline.json      # 真实API性能基线
├── synthetic-baseline.json     # 合成测试基线
└── baseline-history.json       # 历史基线对比
```

## 7. 相关文档

- [PERFORMANCE.md](../../docs/PERFORMANCE.md) - 性能分析方法
- [ENGINEERING.md](../../docs/ENGINEERING.md) - Trace系统详解