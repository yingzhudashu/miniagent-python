# RAG增强计划（RAG Enhancement Plan）

## 背景

当前知识库检索（RAG）存在以下限制：
1. 仅支持关键词匹配（基于keyword_index.py）
2. 缺乏语义理解（无法理解查询意图）
3. 无多轮对话上下文（每次独立检索）
4. 缺少相关性反馈机制

## 增强目标

### Phase 1: 基础增强（已完成）

#### 1.1 嵌入向量检索（embedding_search.py）

**状态**: 已完成（2026-05）

**功能**:
- 向量相似度检索（OpenAI embeddings）
- Top-K结果排序（score≥min_score）
- 支持增量索引更新

**性能**:
- 检索时间：<50ms（Top-K=8）
- 索引容量：支持max_entries=2000

**关键代码**:
- `miniagent/infrastructure/embedding_search.py` - 嵌入搜索引擎
- `miniagent/memory/embedding_index.py` - 嵌入索引存储

**配置**:
```json
{
  "embedding": {
    "enabled": true,
    "base_url": "https://api.openai.com/v1",
    "model": "text-embedding-ada-002",
    "dimension": 1536,
    "top_k": 8,
    "min_score": 0.3
  }
}
```

#### 1.2 混合检索（关键词+嵌入）

**状态**: 已完成（2026-05）

**功能**:
- 关键词索引和嵌入搜索并行执行
- 结果合并策略：关键词优先，嵌入补充
- Top-K合并（关键词8 + 嵌入8）

**性能**:
- 并行执行：总延迟降低40%（vs顺序执行）
- executor.py:494-555 - 并行检索实现

**关键代码**:
```python
# executor.py (已重构为executor_memory.py)
embed_task = asyncio.create_task(
    provider.search(user_input, limit=8, min_score=0.3)
)
kw_task = asyncio.create_task(
    asyncio.to_thread(search_relevant_with_index, ki, user_input, 8, 0)
)
results = await asyncio.gather(embed_task, kw_task)
```

### Phase 2: 进阶增强（进行中）

#### 2.1 多轮对话上下文记忆

**状态**: 规划中（预计2026-06）

**目标**:
- 在检索时注入最近对话历史
- 提升相关性准确率15%
- 支持上下文相关的检索结果排序

**设计方案**:
```python
# 检索时注入对话上下文
context_aware_query = f"{recent_history}\n当前查询：{user_input}"
results = await provider.search(context_aware_query, limit=8)
```

**挑战**:
- 对话历史token消耗（需要压缩）
- 上下文权重平衡（历史 vs 当前）
- 多轮语义理解（LLM辅助）

#### 2.2 相关性反馈机制

**状态**: 规划中（预计2026-06）

**目标**:
- 记录用户对检索结果的选择
- 根据反馈调整相关性权重
- 支持个性化检索排序

**设计方案**:
```python
# 反馈收集
activity_log.log_retrieval_feedback(
    session_key=session_key,
    query=user_input,
    selected_results=[result.entry_key for result in top_results],
    relevance_score=user_rating,  # 1-5分
)

# 权重调整
provider.update_relevance_weights(
    entry_keys=selected_results,
    boost_factor=1.2,
)
```

#### 2.3 动态知识库更新

**状态**: 规划中（预计2026-07）

**目标**:
- 实时添加新知识（用户输入 → 知识库）
- 自动分类和索引
- 知识去重和冲突检测

**设计方案**:
```python
# 动态知识添加
async def add_dynamic_knowledge(
    user_input: str,
    llm_response: str,
    session_key: str,
) -> None:
    # 提取知识点
    facts = extract_facts(user_input + " " + llm_response)

    # 添加到知识库
    kb.add_entry(
        content="\n".join(facts),
        source=f"session/{session_key}",
        tags=["dynamic", "user-generated"],
    )

    # 自动索引
    await kb.update_index()
```

### Phase 3: 高级增强（规划中）

#### 3.1 知识图谱构建

**状态**: 长期规划（预计2026-08）

**目标**:
- 实体关系抽取（LLM辅助）
- 知识图谱存储（Neo4j或内存图）
- 图谱辅助检索（实体关联）

**设计方案**:
```python
# 实体关系抽取
entities = await llm_extract_entities(knowledge_text)
relations = await llm_extract_relations(entities)

# 图谱存储
graph_db.add_entities(entities)
graph_db.add_relations(relations)

# 图谱检索
related_entities = graph_db.find_related(query_entity, depth=2)
results = kb.search_by_entities(related_entities)
```

#### 3.2 实体关系推理

**状态**: 长期规划（预计2026-09）

**目标**:
- 基于图谱的推理（transitive relations）
- 知识补全（推断隐含知识）
- 推理链可视化

**设计方案**:
```python
# 推理引擎
reasoning_engine = KnowledgeReasoningEngine(graph_db)

# 推理查询
reasoning_chain = reasoning_engine.reason(
    start_entity="Python",
    target_entity="数据分析",
    max_depth=5,
)

# 推理结果整合
augmented_knowledge = reasoning_chain.to_text()
context_manager.inject_knowledge(augmented_knowledge)
```

#### 3.3 自动知识提取

**状态**: 长期规划（预计2026-10）

**目标**:
- 从对话自动提取知识点
- 从文档自动构建知识库
- 知识质量评估和过滤

**设计方案**:
```python
# 自动提取流水线
async def auto_knowledge_extraction_pipeline():
    # Step 1: 对话监控
    conversations = monitor_recent_conversations()

    # Step 2: 知识提取
    for conv in conversations:
        knowledge_points = await llm_extract_knowledge(conv)

        # Step 3: 质量评估
        quality_score = await evaluate_knowledge_quality(knowledge_points)

        if quality_score > 0.7:
            # Step 4: 知识添加
            await kb.add_knowledge(knowledge_points)

    # Step 5: 索引更新
    await kb.update_all_indices()
```

## 技术方案

### 混合检索架构（已实现）

详见：
- `miniagent/core/executor_memory.py:retrieve_memory_parallel` - 并行检索
- `miniagent/memory/keyword_index.py` - 关键词索引
- `miniagent/infrastructure/embedding_search.py` - 嵌入搜索

### 配置优化（已实现）

详见：
- `config.defaults.json` - embedding配置节
- `config.defaults.json` - performance.cache配置

### 性能基准（已实现）

详见：
- `tests/evaluation/test_perf_real_api.py` - 真实API性能测试
- `tests/perf_baselines/` - 性能基线目录

## 性能指标

### 当前性能（Phase 1）

- **关键词检索**: <20ms（Top-K=8）
- **嵌入检索**: <50ms（Top-K=8）
- **混合检索**: <70ms（并行执行）
- **准确率**: 70%（关键词） + 75%（嵌入）

### 目标性能（Phase 2）

- **上下文检索**: <80ms（Top-K=8）
- **准确率**: 85%（目标提升15%）
- **反馈系统**: <5ms反馈记录

### 远期目标（Phase 3）

- **图谱检索**: <100ms（深度2）
- **推理查询**: <200ms（5层推理链）
- **自动提取**: 100条/小时

## 依赖和资源

### 已有依赖

- OpenAI embeddings API（text-embedding-ada-002）
- miniagent/memory/keyword_index.py
- miniagent/infrastructure/embedding_search.py

### 待引入依赖

- Neo4j或内存图数据库（Phase 3）
- 推理引擎库（Phase 3）
- 知识质量评估模型（Phase 3）

## 相关文档

- docs/KNOWLEDGE_BASE.md - 知识库使用指南
- docs/ARCHITECTURE.md - 系统架构设计
- docs/MEMORY_SYSTEM.md - 记忆系统架构

## 变更历史

- 2026-05: Phase 1完成（嵌入检索、混合检索）
- 2026-06-05: 创建文档（Phase 2任务）
- 预计2026-06: Phase 2启动（上下文记忆、反馈机制）
- 预计2026-07: Phase 2完成
- 预计2026-08: Phase 3启动（知识图谱）