# 实例注册表（Instance Registry）

## 概述

实例注册表管理MiniAgent的全局实例，包括：
- 工具注册表（ToolRegistry）
- 会话管理器（SessionManager）
- 配置管理器（ConfigManager）
- 知识库注册表（KnowledgeBaseRegistry）

## 设计目标

1. 避免全局变量散布
2. 提供统一的实例获取接口
3. 支持依赖注入和测试mock
4. 单例模式管理共享实例

## 核心API

### get_global_tool_registry()

获取全局工具注册表。

```python
from miniagent import get_global_tool_registry

registry = get_global_tool_registry()
read_tool = registry.get("read_file")
```

### get_session_manager()

获取会话管理器。

```python
from miniagent.session import get_session_manager

manager = get_session_manager()
session = manager.get_or_create("test-session")
```

### get_kb_registry()

获取知识库注册表。

```python
from miniagent.knowledge import get_kb_registry

kb_registry = get_kb_registry()
kb = kb_registry.get("default")
```

## 实现细节

详见 `miniagent/__init__.py`、`miniagent/infrastructure/__init__.py`、`miniagent/session/__init__.py`。

## 使用指南

### 正常使用

直接调用get_*函数获取实例：

```python
from miniagent import get_global_tool_registry
from miniagent.session import get_session_manager

registry = get_global_tool_registry()
manager = get_session_manager()
```

### 测试场景

使用依赖注入替代全局实例：

```python
# 测试代码
from unittest.mock import Mock
from miniagent import set_global_tool_registry

# 创建mock工具注册表
mock_registry = MockToolRegistry()
set_global_tool_registry(mock_registry)

# 现在get_global_tool_registry()返回mock实例
registry = get_global_tool_registry()
assert registry == mock_registry
```

## 设计原则

1. **单例模式**：全局实例使用单例模式避免重复创建
2. **线程安全**：使用线程锁保护全局实例初始化
3. **延迟初始化**：实例在首次访问时初始化（lazy load）
4. **可替换**：支持set_*函数替换全局实例（用于测试）

## 架构重构进展

根据2026-06-05的重构计划，实例注册表正在向依赖注入系统迁移：

### 当前状态（传统模式）

- 全局变量：`miniagent/__init__.py`中的`_global_tool_registry`
- 全局变量：`miniagent/session/__init__.py`中的`_session_manager`

### 目标状态（依赖注入）

详见Phase 3计划 - 使用`DependencyContainer`替代全局状态：

```python
# 目标架构
from miniagent.infrastructure.container import get_tool_registry

# 依赖注入获取实例
registry = get_tool_registry(ToolRegistryProtocol)
```

## 相关文档

- docs/ARCHITECTURE.md - 系统架构设计
- miniagent/types/protocols.py - Protocol接口定义（Phase 3新增）
- miniagent/infrastructure/container.py - 依赖注入容器（Phase 3新增）

## 变更历史

- 2026-06-05: 创建文档（Phase 2任务）
- 待定: Phase 3完成依赖注入系统迁移