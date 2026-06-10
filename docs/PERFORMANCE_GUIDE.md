# Mini Agent Python 性能优化指南

> 版本: 2.1.0 | 最后更新: 2026-06-09

本指南提供性能优化策略，帮助提升 Agent 响应速度和资源利用率。

---

## 目录

1. [内存优化](#内存优化)
2. [执行优化](#执行优化)
3. [网络优化](#网络优化)
4. [监控与诊断](#监控与诊断)
5. [最佳实践](#最佳实践)

---

## 内存优化

### 历史压缩策略

**问题**：会话历史过长，占用内存过多

**优化方案**：
1. **限制历史长度**：
   ```json
   {
     "memory": {
       "history_tail_messages": 100  // 保留最近100条
     }
   }
   ```

2. **启用自动归档**：
   ```json
   {
     "memory": {
       "archive_enabled": true,
       "archive_after_days": 30  // 30天后自动归档
     }
   }
   ```

3. **定期清理**：
   ```bash
   # 每周清理超过90天的会话
   python scripts/cleanup_old_sessions.py --days 90
   ```

---

### 记忆分层清理

**问题**：三层记忆文件膨胀

**优化方案**：
1. **短期记忆**：
   - 缓存最多 `memory.store_cache_max` 个会话（默认 200）
   - 超过限制自动清理

2. **活动日志**：
   ```json
   {
     "memory": {
       "activity_log_retention_days": 90  // 保留90天
     }
   }
   ```

3. **关键词索引**：
   ```json
   {
     "memory": {
       "keyword_index_max": 15000  // 减少索引大小
     }
   }
   ```

---

### 缓存大小调整

**问题**：缓存占用内存过高

**优化方案**：
1. **工具注册表缓存**：
   ```json
   {
     "memory": {
       "registry_max_entries": 2000  // 从3000减少到2000
     }
   }
   ```

2. **飞书去重缓存**：
   - 自动刷盘（每60秒或1000条）
   - 进程退出时同步保存

3. **嵌入搜索缓存**：
   ```json
   {
     "memory": {
       "embedding_enabled": false  // 不需要时可关闭
     }
   }
   ```

---

## 执行优化

### 并行工具调用

**问题**：多个工具串行执行，总耗时 = 各工具耗时之和

**优化方案**：
```json
{
  "agent": {
    "allow_parallel_tools": true  // 启用并行调用
  }
}
```

**效果**：总耗时 ≈ 最慢单个工具耗时（提升50-70%）

**适用场景**：
- 多个独立文件读取
- 多个独立搜索操作
- 无依赖关系的工具调用

---

### 流式处理配置

**问题**：等待完整响应，用户体验差

**优化方案**：
```json
{
  "agent": {
    "streaming": true  // 启用流式响应
  }
}
```

**效果**：
- 用户实时看到思考过程
- 总响应时间不变，但体验提升明显

---

### Token 估算优化

**问题**：Token 估算不准确，频繁触发上下文压缩

**优化方案**：
1. **精确估算**：
   - 使用 tiktoken 库精确计数
   - 估算公式优化（见 `executor.py`）

2. **调整预算**：
   ```json
   {
     "model": {
       "context_window": 128000,  // 根据实际模型调整
       "max_tokens": 4096
     }
   }
   ```

3. **预压缩策略**：
   ```json
   {
     "memory": {
       "compression_threshold": 0.8  // 80%时开始压缩
     }
   }
   ```

---

## 网络优化

### API 调用优化

**问题**：OpenAI API 响应慢

**优化方案**：
1. **调整超时时间**：
   ```json
   {
     "model": {
       "api_timeout": 60  // 从30秒增加到60秒
     }
   }
   ```

2. **启用重试**：
   ```json
   {
     "model": {
       "max_retries": 3  // 自动重试3次
     }
   }
   ```

3. **使用更快的模型**：
   ```json
   {
     "model": {
       "model": "gpt-4o-mini"  // 比 gpt-4o 快2-3倍
     }
   }
   ```

---

### 飞书连接优化

**问题**：飞书 WebSocket 连接不稳定

**优化方案**：
1. **自动重连**：
   ```json
   {
     "feishu": {
       "websocket": {
         "auto_reconnect": false,  // 使用外层重连（更可靠）
         "watchdog_interval": 30,
         "dead_conn_grace": 90
       }
     }
   }
   ```

2. **定期刷新**：
   ```json
   {
     "feishu": {
       "websocket": {
         "refresh_interval": 3600  // 每小时主动刷新
       }
     }
   }
   ```

---

## 监控与诊断

### 性能指标收集

**查看工具调用统计**：
```bash
/stats
```

**输出示例**：
```
工具名称            调用次数    平均耗时    成功率
exec_command         120       1250ms      66.7%
read_file             85       150ms       98.8%
web_search            45       2500ms      91.1%
```

---

### 热点分析工具

**使用性能分析脚本**：
```bash
python scripts/perf_profile_tracemalloc.py
```

**分析内存热点**：
```python
import tracemalloc
tracemalloc.start()

# 运行 Agent

snapshot = tracemalloc.take_snapshot()
for stat in snapshot.statistics('lineno')[:10]:
    print(stat)
```

---

### 优化效果验证

**运行性能测试**：
```bash
pytest -m perf tests/test_perf_synthetic.py -xvs
```

**对比指标**：
- 响应时间：从 X 秒减少到 Y 秒
- 内存占用：从 X MB减少到 Y MB
- 成功率：从 X% 提升到 Y%

---

## 最佳实践

### 1. 定期维护

**每周执行**：
```bash
# 清理旧会话
python scripts/cleanup_old_sessions.py --days 90

# 清理旧记忆
rm -f workspaces/memory/*.md  # 手动清理超过180天的记忆

# 查看性能统计
/stats
```

---

### 2. 监控关键指标

**关键指标**：
- 内存占用 < 500 MB
- 平均响应时间 < 30 秒
- 工具成功率 > 95%
- LLM Token 使用率 < 80%

---

### 3. 优化配置模板

**生产环境推荐配置**：
```json
{
  "memory": {
    "history_tail_messages": 100,
    "archive_enabled": true,
    "archive_after_days": 30,
    "store_cache_max": 200,
    "keyword_index_max": 15000
  },
  "agent": {
    "streaming": true,
    "allow_parallel_tools": true,
    "tool_call_timeout": 60
  },
  "model": {
    "model": "gpt-4o-mini",
    "api_timeout": 60,
    "max_retries": 3
  }
}
```

---

## 性能问题排查清单

1. ✅ 内存占用过高？ → 清理历史和记忆
2. ✅ 响应缓慢？ → 启用流式处理和并行工具
3. ✅ API 超时？ → 增加超时时间和重试次数
4. ✅ 飞书无响应？ → 检查连接状态和凭证
5. ✅ Token 超限？ → 调整上下文窗口和压缩策略

---

**持续优化**：性能优化是一个持续过程，建议定期检查和调整配置。
