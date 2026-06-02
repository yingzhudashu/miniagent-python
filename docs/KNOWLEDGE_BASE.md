# MiniAgent 知识库系统

MiniAgent 支持快速挂载本地知识库、文档、资料，通过关键词索引检索并注入到 Agent 上下文。

## 概述

知识库系统提供以下核心功能：

- **挂载知识库**：将本地目录或文件挂载为知识库
- **关键词检索**：基于 TF-IDF 的关键词索引，快速检索相关内容
- **跨知识库搜索**：支持同时检索多个知识库
- **持久化状态**：挂载状态自动保存，重启后自动恢复

## 架构

```
miniagent/knowledge/
├── __init__.py      # 模块入口
├── base.py          # KnowledgeBase 类（单知识库）
└── registry.py      # KnowledgeRegistry 类（注册表）
```

### 数据流

```
用户输入 → 知识库检索 → kb_context → 注入 system prompt → LLM 调用
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
retriever: keyword     # 检索策略（keyword / fulltext）
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

## 检索机制

### 关键词提取

复用 `miniagent.memory.keyword_index` 的关键词提取逻辑：

- 中英文分词
- 3-gram 权重加高
- TF-IDF 排序

### 检索流程

1. 提取查询关键词
2. 计算每个条目的匹配分数
3. 按分数排序返回 top_k 条目
4. 截断到 max_chars 字符数

### 可选增强

环境变量 `MINIAGENT_EMBEDDING_ENABLED=1` 启用向量检索（需要嵌入模型）。

## 配置选项

| 环境变量 | 说明 | 默认值 |
|----------|------|--------|
| `MINIAGENT_KB_ROOT` | 知识库根目录 | `workspaces/knowledge` |
| `MINIAGENT_KB_AUTO_MOUNT` | 自动挂载根目录下的知识库 | `1` |
| `MINIAGENT_KB_MAX_CHARS` | 跨知识库检索最大字符数 | `8000` |

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

# 启动 MiniAgent，自动挂载
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
    def __init__(self, path: str, config: KBConfig | None = None):
        self._path = path
        self._config = config or load_kb_config(path)
        self._entries: list[KnowledgeEntry] = []
        self._index: dict[str, list[int]] = {}

    def load(self) -> None:
        # 扫描文件、构建索引

    def search(self, query: str, top_k: int = 5, max_chars: int = 8000) -> str:
        # 检索并返回格式化结果

    def reload(self) -> None:
        # 重新加载
```

### KnowledgeRegistry 类

```python
class KnowledgeRegistry:
    def __init__(self, state_dir: str | None = None):
        self._mounted: dict[str, KnowledgeBase] = {}
        self._load_registry()  # 加载持久化状态
        self._auto_mount()     # 自动挂载默认知识库

    def mount(self, path: str, name: str | None = None) -> dict:
        # 挂载知识库

    def unmount(self, name: str) -> dict:
        # 卸载知识库

    def search(self, query: str, kb_name: str | None = None) -> str:
        # 跨知识库检索
```

### 持久化

挂载状态保存到 `kb_registry.json`：

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
- **单例模式**：进程级共享注册表
- **字符截断**：避免大文件内存溢出
- **复用关键词提取**：不引入额外索引模块

## 与记忆系统的关系

知识库与三层记忆系统互补：

| 系统 | 数据来源 | 检索方式 | 用途 |
|------|----------|----------|------|
| Session Memory | 会话历史 | 关键词 + 向量 | 上下文延续 |
| Activity Log | 操作记录 | 关键词 | 行为追溯 |
| Knowledge Base | 外部文档 | 关键词 | 知识注入 |

知识库内容不写入记忆系统，仅作为临时上下文注入。

## 最佳实践

1. **按主题分库**：不同项目/领域使用独立知识库
2. **限制文件大小**：单文件不超过 50KB，避免截断
3. **使用 KB.yaml**：配置合适的 file_patterns
4. **定期重载**：文档更新后 `/kb reload`
5. **精简检索词**：关键词越精确，匹配越准确