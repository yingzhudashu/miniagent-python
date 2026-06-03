# MiniAgent 性能优化计划

## 执行日期：2026-06-03

## 优化目标
- 减少内存占用（transcript上限、Session LRU）
- 降低CPU消耗（正则预编译、JSON缓存）
- 优化IO阻塞（异步文件读取）
- 保留所有功能，不影响用户体验

## 阶段1：高严重度问题（立即处理）

### 1.1 正则表达式预编译（48个文件）

**问题**：高频正则每次都重新编译，浪费CPU

**优化文件列表**：
- miniagent/engine/main.py (文件标记检测)
- miniagent/core/executor.py (思考标签排序)
- miniagent/feishu/poll_server.py (Markdown转换)
- miniagent/engine/markdown_cli.py (标题检测)
- miniagent/memory/store.py (事实提取)
- 其他44个文件

**优化策略**：
```python
# 模块级预编译
_PATTERN_NAME = re.compile(r"pattern_string")

# 使用时直接调用
matches = _PATTERN_NAME.findall(text)
```

### 1.2 transcript 内存优化

**问题**：
- 上限400_000字符过大
- trim算法每次遍历整个列表（O(n)）

**优化策略**：
- 降低上限到200_000
- 维护累计长度计数器，避免每次遍历

### 1.3 流式输出缓冲优化

**问题**：
- 每100个chunk合并一次，仍有性能问题
- getvalue()需要拼接所有chunk

**优化策略**：
- 每50个chunk合并一次
- 简化getvalue()逻辑

### 1.4 JSON解析缓存

**问题**：工具参数可能重复解析

**优化策略**：
- 第一次解析后缓存到对象属性
- 后续直接使用缓存值

## 阶段2：中严重度问题

### 2.1 终端宽度缓存TTL增加
- 从2秒增加到5秒

### 2.2 Markdown渲染缓存扩大
- 缓存大小从100增加到200

### 2.3 Session状态LRU驱逐
- 最大50个会话状态

### 2.4 文件IO异步化
- 历史加载使用asyncio.to_thread

## 执行顺序

1. 先处理高频调用路径（main.py, executor.py）
2. 再处理辅助模块（markdown_cli, thinking）
3. 最后处理架构优化（Session LRU）

## 验证方法

- 每个优化后运行pytest
- 性能基准测试对比
- 内存占用监控

## 回滚策略

- 每个优化独立提交
- 出现问题可单独回滚
- 保留性能优化开关（环境变量）