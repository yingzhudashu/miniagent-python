# Mini Agent Python — 知识库系统

> Mini Agent Python | 版本: 3.0.0 | 最后更新: 2026-07-15 | 与 `miniagent.__version__` 对齐

Mini Agent 支持快速挂载本地知识库、文档、资料，通过关键词索引检索并拼入 Agent 上下文。

## 概述

知识库系统提供以下核心功能：

- **挂载知识库**：将本地目录或文件挂载为知识库
- **关键词检索**：基于关键词倒排索引与子串匹配，快速检索相关内容
- **跨知识库搜索**：支持同时检索多个知识库，合并打分后取全局 top_k
- **持久化状态**：挂载状态自动保存，重启后自动恢复
- **文件分析复用**：`read_file` 成功读取文本文件后，可自动写入项目级 `_auto_file_analysis` 知识库，后续同项目分析优先通过 RAG 复用

## 架构

```
miniagent/assistant/knowledge/
├── __init__.py      # 模块入口
├── base.py          # KnowledgeBase 类（单知识库）
├── file_ingest.py   # 文件分析自动入库
└── registry.py      # KnowledgeRegistry 类（注册表）
```

### 数据流

```
用户输入 → 知识库检索 → kb_context → current turn user context → LLM 调用
```

## 使用方式

### CLI 命令

```bash
# 列出已挂载的知识库
/kb list

# 挂载知识库
/kb mount /path/to/docs
/kb mount /path/to/docs my_kb_name

# 检索知识库
/kb search API 文档
/kb search API 文档 my_kb_name

# 卸载知识库
/kb unmount my_kb_name

# 重新加载知识库
/kb reload my_kb_name
/kb reload  # 重载所有
```

### Agent 工具

Agent 可通过以下工具访问知识库：

| 工具名称 | 功能 | 参数 |
|----------|------|------|
| `search_knowledge` | 检索知识库 | query, kb_name(可选), top_k(可选) |
| `read_knowledge_file` | 读取完整文件 | kb_name, file_path |
| `kb_list` | 列出知识库 | 无 |

## 知识库目录结构

知识库目录应有以下结构之一：

### 方式一：KB.yaml 配置文件

```
my_kb/
├── KB.yaml        # 配置文件
├── files/         # 文件目录
│   ├── doc1.md
│   ├── doc2.txt
│   └── data.json
```

KB.yaml 格式：

```yaml
name: my_kb            # 知识库名称
description: 项目文档  # 描述
retriever: keyword     # 检索策略：keyword（关键词倒排）或 fulltext（子串匹配）
max_chars: 8000        # 单次检索最大字符数
top_k: 5               # 返回条目数
file_patterns:         # 包含的文件模式
  - "*.md"
  - "*.txt"
  - "*.json"
```

### 方式二：files/ 目录

```
my_kb/
└── files/
    ├── doc1.md
    └── doc2.txt
```

无 KB.yaml 时，知识库名称默认为目录名。

### 自动文件分析知识库

当 `knowledge.auto_ingest_analyzed_files=true` 时，文件工具 `read_file` 读取文本文件成功后会把源文件写入项目级自动知识库：

```
workspaces/knowledge/_auto_file_analysis/
├── KB.yaml
├── source-metadata.json
└── files/
    └── <source-path-hash>.<ext>
```

`source-metadata.json` 记录原始源文件位置和变更指纹：

```json
{
  "/absolute/source/path/app.py": {
    "source_path": "/absolute/source/path/app.py",
    "file_path": "a1b2c3d4e5f6.py",
    "source_hash": "sha256...",
    "mtime": 1780800000.0,
    "size": 12345,
    "ingested_at": 1780800001.0,
    "display_path": "relative/or/absolute/path"
  }
}
```

同一源文件未变化时不会重复写入；内容 hash 变化后会刷新镜像文件并 reload 自动知识库。检索结果会展示 `source_path`、hash 摘要和 size，便于回答时标注来源。RAG 未命中或源文件可能变化时，Agent 仍应二次读取源文件确认，不能把旧索引当成唯一真相源。

## 检索机制

### 关键词提取

复用 `miniagent.assistant.memory.keyword_index` 的关键词提取逻辑：

- 中英文分词（中文 2/3-gram，英文词元）
- 3-gram 命中权重更高

### 检索策略

| `retriever` | 行为 |
|-------------|------|
| `keyword`（默认） | 查询词提取关键词，与条目关键词倒排索引匹配计分 |
| `fulltext` | 在条目全文中做不区分大小写的子串匹配计分 |

### 检索流程

1. 提取查询关键词（或子串）
2. 计算每个条目的匹配分数
3. 按分数排序返回 top_k 条目（跨库检索时合并全局 top_k）
4. 截断到 max_chars 字符数

### 与记忆系统向量检索的区别

Session Memory 等模块在 `embedding.enabled=true` 时可使用向量检索。**知识库模块当前仅使用关键词/子串检索**，不依赖 embedding 配置。

## 配置选项

| JSON 路径 | 说明 | 默认值 |
|----------|------|--------|
| `knowledge.root` / `knowledge.default_root` | 知识库根目录 | `workspaces/knowledge` |
| `knowledge.auto_mount` | 自动挂载根目录下的知识库 | `true` |
| `knowledge.top_k` | 跨知识库检索默认条目数 | `5` |
| `knowledge.max_chars` | 跨知识库检索最大字符数 | `8000` |
| `knowledge.max_file_chars` | 单文件加载/读取字符上限 | `50000` |
| `knowledge.as_core` | knowledge 工具作为核心工具箱（始终可用） | `true` |
| `knowledge.executor_enabled` | 执行阶段是否自动检索知识库 | `true` |
| `knowledge.executor_top_k` / `knowledge.executor_max_chars` | 执行阶段检索条数与字符上限 | `3` / `4000` |
| `knowledge.planner_enabled` / `planner_top_k` / `planner_max_chars` | 规划阶段 RAG | `true` / `3` / `4000` |
| `knowledge.clarifier_enabled` / `clarifier_top_k` / `clarifier_max_chars` | 澄清阶段 RAG | `true` / `3` / `4000` |
| `knowledge.classifier_enabled` / `classifier_top_k` / `classifier_max_chars` | 分类阶段 RAG | `true` / `3` / `4000` |
| `knowledge.reflector_enabled` / `reflector_top_k` / `reflector_max_chars` | 反思阶段 RAG | `true` / `3` / `4000` |
| `knowledge.auto_ingest_analyzed_files` | 文件分析自动入库 | `true` |
| `knowledge.auto_ingest_kb_name` | 自动入库知识库名称 | `_auto_file_analysis` |
| `knowledge.auto_ingest_max_file_chars` | 自动入库单文件字符上限；为空时沿用 `max_file_chars` | `null` |

## 示例

### 挂载项目文档

```bash
# 创建知识库目录
mkdir -p workspaces/knowledge/project_docs/files

# 复制文档
cp docs/*.md workspaces/knowledge/project_docs/files/

# 创建配置
cat > workspaces/knowledge/project_docs/KB.yaml << EOF
name: project_docs
description: 项目文档
file_patterns:
  - "*.md"
EOF

# 启动 Mini Agent，自动挂载
python -m miniagent

# Agent 中检索
/kb search API 接口
```

### Agent 工具调用

```json
{
  "name": "search_knowledge",
  "arguments": {
    "query": "如何配置飞书",
    "kb_name": "project_docs",
    "top_k": 3
  }
}
```

## 实现细节

### KnowledgeBase 类

```python
class KnowledgeBase:
    def load(self) -> None:
        # 扫描文件、构建索引

    def rank_entries(self, query: str) -> list[tuple[int, float]]:
        # keyword 或 fulltext 打分

    def search(self, query: str, top_k: int = 5, max_chars: int = 8000) -> str:
        # 检索并返回格式化结果

    def reload(self) -> None:
        # 重新加载
```

### KnowledgeRegistry 类

```python
class KnowledgeRegistry:
    def mount(self, path: str, name: str | None = None) -> dict:
        # 挂载知识库（自定义名称会写入 kb_registry.json）

    def search(self, query: str, kb_name: str | None = None) -> str:
        # 单库或跨库检索（跨库合并全局 top_k）

    def refresh_auto_file_kb(self, path: str, name: str) -> dict:
        # 自动入库后热更新
```

### 持久化

挂载状态保存到 `kb_registry.json`（使用注册表中的挂载名称，而非仅 KB.yaml 内的 name）：

```json
{
  "mounted": [
    {
      "name": "project_docs",
      "path": "/path/to/project_docs",
      "mounted_at": 1704067200.0
    }
  ],
  "updated_at": 1704067200.0
}
```

## 性能优化

- **懒加载**：首次检索时才加载索引
- **显式所有权**：`ApplicationContainer.knowledge_registry` 持有进程内唯一注册表，并沿命令、RAG、工具和文件摄取边界显式注入
- **字符截断**：避免大文件内存溢出
- **复用关键词提取**：不引入额外索引模块
- **扫描去重**：重叠 `file_patterns` 不会重复索引同一文件

## 与记忆系统的关系

知识库与三层记忆系统互补：

| 系统 | 数据来源 | 检索方式 | 用途 |
|------|----------|----------|------|
| Session Memory | 会话历史 | 关键词 + 可选向量 | 上下文延续 |
| Activity Log | 操作记录 | 关键词 | 行为追溯 |
| Knowledge Base | 外部文档 | 关键词 / 子串 | 知识注入 |

知识库内容不写入记忆系统，仅作为临时上下文拼入当前轮 prompt。

## Agent 如何使用知识库（RAG 全面集成）

Mini Agent 在 Agent 各核心阶段使用 RAG：

### 主动检索模式（工具层）

默认 `knowledge.as_core=true` 时，knowledge 工具为核心工具箱（`toolbox=None`），始终可用：

- **search_knowledge**：Agent 可主动检索知识库
- **read_knowledge_file**：读取完整文件（路径限制在知识库目录内）
- **kb_list**：查看已挂载知识库

设为 `knowledge.as_core=false` 时，工具归入 `knowledge` 工具箱，需规划阶段选中该工具箱才可用。

### 自动注入模式（各阶段）

| 阶段 | RAG 增强方式 |
|------|-------------|
| **执行阶段** | 自动检索，放入 current turn user context |
| **规划阶段** | 检索摘要，辅助判断是否需要 knowledge 工具箱 |
| **需求澄清** | 检索已有文档，避免重复提问 |
| **任务分类** | 检索摘要，辅助难度判断 |
| **反思评估** | 检索标准文档，辅助质量评估 |

统一入口：`miniagent.assistant.knowledge.retrieve_knowledge_context(query, phase=...)`

## 最佳实践

1. **按主题分库**：不同项目/领域使用独立知识库
2. **限制文件大小**：单文件不超过 50KB，避免截断
3. **使用 KB.yaml**：配置合适的 file_patterns 与 retriever
4. **定期重载**：文档更新后 `/kb reload`
5. **精简检索词**：关键词越精确，匹配越准确
6. **关键结论二次确认**：自动入库/RAG 片段不能替代源文件读取
