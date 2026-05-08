# Mini Agent Python — 全功能自检测 & 分步测试计划

**日期**: 2026-05-08  
**版本**: Python 重写版  
**审计范围**: 全部 src/ 模块，3 种启动模式

---

## 📋 一、架构总览

```
三种启动模式：
┌─────────────────┬──────────────────┬────────────────────┐
│ CLI 模式         │ 飞书模式          │ 统一模式 (unified)  │
│ python -m src   │ python -m src    │ python -m src      │
│ --standalone    │ --feishu         │ --unified [--feishu]│
├─────────────────┼──────────────────┼────────────────────┤
│ cli.py          │ poll_server.py   │ unified.py         │
│ 独立 session_mgr│ 独立 session_mgr │ 共享 UnifiedEngine │
│ history.json    │ history.json     │ 共享 session_mgr   │
└─────────────────┴──────────────────┴────────────────────┘

共享核心：
  agent.py → planner.py → executor.py → tools/*
  session/manager.py, core/config.py, core/loop_detector.py
  skills/*, security/*, types/*
```

---

## 🔍 二、模块级自检测结果

### ✅ 2.1 入口与模式路由 (`__main__.py`)
| 检查项 | 状态 | 备注 |
|--------|------|------|
| 3 种模式路由 | ✅ | `--feishu` / `--unified` / 默认 CLI |
| .env 加载 | ✅ | python-dotenv fallback |
| 异常兜底 | ✅ | 未知模式回退到 CLI |
| **⚠️ 潜在问题** | ⚠️ | 默认模式（无参数）走 CLI 但会检测飞书桥接，若端口被占用但 HTTP 无响应可能短暂超时（已修复：HTTP 健康检查） |

### ✅ 2.2 CLI 模式 (`cli/cli.py`)
| 检查项 | 状态 | 备注 |
|--------|------|------|
| 单实例锁 | ✅ | try/force acquire + PID 文件 |
| 内置命令 | ✅ | .stats/.skills/.sessions/.session/.profile/.plan/.stop |
| 会话新建 | ✅ | `.session new` 含 load_session_history() |
| 会话切换 | ✅ | `.session switch` 含 load_session_history() |
| 会话销毁 | ✅ | `.session destroy` 持久化到磁盘 |
| 历史持久化 | ✅ | 每轮 save_session_history() |
| 桥接检测 | ✅ | HTTP POST health check (2s timeout) |
| 桥接超时降级 | ✅ | inject_reply 失败 → bridge_available=False |
| 信号处理 | ✅ | SIGINT/SIGTERM → 优雅退出 |
| **⚠️ 潜在问题** | ⚠️ | `.sessions` 展示仅列 session_manager 的会话，不显示飞书会话（仅统一模式支持） |

### ✅ 2.3 统一模式 (`unified.py`)
| 检查项 | 状态 | 备注 |
|--------|------|------|
| 共享引擎 | ✅ | UnifiedEngine 管理 CLI + 飞书 |
| ThinkingDisplay | ✅ | 带会话 ID 前缀区分 |
| 消息注入 | ✅ | .send 命令注入到任意会话 |
| 会话路由 | ✅ | run_agent_with_thinking 统一入口 |
| 历史持久化 | ✅ | 每轮 save_session_history() |
| 飞书后台任务 | ✅ | asyncio.create_task 不阻塞 CLI |
| **⚠️ 潜在问题** | ⚠️ | `_feishu_sessions` 缓存与 `session_ctx.conversation_history` 指向同一对象，truncate 时 `self._feishu_sessions[chat_id] = history[-40:]` 会创建新 list 导致引用断裂 |

### ✅ 2.4 飞书 WebSocket (`feishu/poll_server.py`)
| 检查项 | 状态 | 备注 |
|--------|------|------|
| SDK WSClient | ✅ | 长轮询模式 |
| 消息去重 | ✅ | 内存 set + 磁盘 dedup.json |
| 聊天室队列 | ✅ | per-chat asyncio.Queue 顺序处理 |
| 消息防抖 | ✅ | DEBOUNCE_WINDOW 合并短时消息 |
| 优雅关闭 | ✅ | SIGINT/SIGTERM + loop.close |
| 状态 API | ✅ | HTTP :18789 返回 status |
| 健康检查 | ✅ | `{"action":"status"}` → 200 |
| **⚠️ 潜在问题** | ⚠️ | dedup.json 无限增长，无清理机制 |

### ✅ 2.5 Agent 核心 (`core/agent.py`)
| 检查项 | 状态 | 备注 |
|--------|------|------|
| 两阶段架构 | ✅ | Planning → Execution |
| 记忆检索 | ✅ | keyword_index.search_relevant |
| 模型预设切换 | ✅ | apply_model_profile |
| 配置合并 | ✅ | merge_agent_config |
| thinking 回调 | ✅ | on_thinking async callback |
| **⚠️ 潜在问题** | ⚠️ | Planning 阶段创建新的 DefaultContextManager，Executor 阶段使用外部注入的 context_manager — 两者独立，planning 的 token 估算不影响 executor |

### ✅ 2.6 Planner (`core/planner.py`)
| 检查项 | 状态 | 备注 |
|--------|------|------|
| 完整计划生成 | ✅ | LLM → JSON parse → 3 次重试 |
| Markdown 代码块处理 | ✅ | 自动剥离 ```json 包裹 |
| 跳过规划 | ✅ | skip_planning → 直接执行 |
| Fallback 兜底 | ✅ | 全部失败返回简单计划 |
| **✅ 正常** | ✅ | JSON 解析失败由 try/except 兜底，无需额外修复 |

### ✅ 2.7 Executor (`core/executor.py`)
| 检查项 | 状态 | 备注 |
|--------|------|------|
| ReAct 循环 | ✅ | tool_call → execute → append → repeat |
| 最大轮次 | ✅ | max_turns 限制 |
| 循环检测 | ✅ | loop_detector.check() |
| 并行工具调用 | ✅ | allow_parallel_tools |
| 错误处理 | ✅ | 工具失败不中断循环 |
| 无工具调用退出 | ✅ | `return final_reply` 正确退出循环 |
| 工具选择 | ✅ | toolbox 策略 |
| 工具监控 | ✅ | monitor.record |
| **✅ 正常** | ✅ | 无明显问题

### ✅ 2.8 上下文管理 (`core/context_manager.py`)
| 检查项 | 状态 | 备注 |
|--------|------|------|
| Token 估算 | ✅ | 中文 1.5/字 + ASCII 4字符/token |
| 上下文压缩 | ✅ | 保留 system + 首条用户 + 最近 2 轮 |
| 记忆注入 | ✅ | inject_memory 追加到 system prompt |
| Token 报告 | ✅ | get_token_report() |
| **⚠️ 潜在问题** | ⚠️ | `_get_available_budget` 硬编码 10% 输出预留，与 config 中 response_format 无关 |

### ✅ 2.9 会话管理 (`session/manager.py`)
| 检查项 | 状态 | 备注 |
|--------|------|------|
| 会话创建 | ✅ | get_or_create + SessionOptions |
| 会话切换 | ✅ | switch 返回新 context |
| 会话销毁 | ✅ | destroy 持久化后删除 |
| 历史持久化 | ✅ | save/load_session_history → history.json |
| 工作空间隔离 | ✅ | per-session files/skills/config |
| 实例 PID | ✅ | instance.pid 文件 |
| **✅ 已修复** | ✅ | standalone 模式 .session new/switch 现在正确调用 load_session_history() |

### ✅ 2.10 循环检测 (`core/loop_detector.py`)
| 检查项 | 状态 | 备注 |
|--------|------|------|
| generic_repeat | ✅ | 相同工具+相同参数 |
| poll_no_progress | ✅ | 连续调用结果无变化 |
| ping_pong | ✅ | A→B→A→B→A→B 交替 |
| 渐进式响应 | ✅ | warning → critical → 拦截 |
| 可配置 | ✅ | 所有阈值可调 |
| **✅ 正常** | ✅ | 无明显问题 |

### ✅ 2.11 关键词索引 (`core/keyword_index.py`)
| 检查项 | 状态 | 备注 |
|--------|------|------|
| 中文分词 | ✅ | 2-gram + 3-gram |
| 英文分词 | ✅ | 空格分词 + 停用词过滤 |
| 倒排索引 | ✅ | keyword → [references] |
| 持久化 | ✅ | load/save keyword-index.json |
| 过期清理 | ✅ | prune_expired(days_old) |
| **⚠️ 潜在问题** | ⚠️ | prune_expired 仅清理引用不清理空关键词（已修复：有空关键词清理逻辑） |

### ✅ 2.12 安全沙箱 (`security/sandbox.py`)
| 检查项 | 状态 | 备注 |
|--------|------|------|
| 路径解析 | ✅ | resolve_sandbox_path 限制在 allowed_dirs |
| 默认工作空间 | ✅ | get_default_workspace → state/workspace |
| **✅ 正常** | ✅ | 无明显问题 |

### ✅ 2.13 进程追踪 (`core/process_tracker.py`)
| 检查项 | 状态 | 备注 |
|--------|------|------|
| 创建追踪 | ✅ | create_tracked_subprocess 加入 _tracked_processes |
| 注销追踪 | ✅ | deregister_process 从列表移除 |
| 清理孤儿 | ✅ | cleanup_all_processes 终止所有追踪进程 |
| **✅ 正常** | ✅ | 无明显问题 |

### ✅ 2.14 工具集
| 工具模块 | 检查项 | 状态 | 备注 |
|----------|--------|------|------|
| filesystem | 8 个文件操作 | ✅ | read/write/edit/list/create/move/copy/delete |
| exec | 命令执行 | ✅ | 超时控制 + 黑名单过滤 + 进程追踪 |
| web | 网页抓取 + 时间 | ✅ | httpx/urllib fallback + HTML 清理 |
| self_opt | 自我优化 | ✅ | inspect/proposal/tests/git_snapshot |
| skills | 技能管理 | ✅ | search/install/list + ClawHub |

---

## 🚨 三、已确认的 Bug & 风险清单

### 🔴 P0 — 已修复
| # | 问题 | 状态 | 修复说明 |
|---|------|------|----------|
| 1 | **统一模式历史引用断裂**：`run_agent_with_thinking` 中 `self._feishu_sessions[chat_id] = history[-40:]` 创建新 list，导致后续 `session_ctx.conversation_history` 与新 list 断开 | ✅ 已修复 | 改为原地截断 `del history[:len(history) - 40]`，保持引用一致 |

### 🟡 P1 — 建议修复
| # | 问题 | 影响模块 | 状态 | 影响 |
|---|------|----------|------|------|
| 2 | **poll_server dedup.json 无限增长**：去重集合只追加不清理 | poll_server.py | 待修复 | 长期运行后内存/磁盘占用增加 |
| 3 | **install_skill 下载成功未校验**：`client.download` 结果未检查文件是否完整 | skills/clawhub_client.py | 待修复 | 可能安装不完整的技能包 |
| 4 | **exec_command 空命令检查缺失** | exec.py | ✅ 已修复 | 添加 `.strip()` + 空值检查 |

### 🟢 P2 — 优化建议
| # | 问题 | 影响模块 | 建议 |
|---|------|----------|------|
| 6 | context_manager 硬编码 10% 输出预留 | context_manager.py | 使用 config 中的值 |
| 7 | standalone .sessions 不显示飞书会话 | cli.py | 仅在统一模式下才需要 |
| 8 | memory_store.py 无大小限制 | memory_store.py | 长期运行可能膨胀 |
| 9 | skills 热加载未实现 | skills/loader.py | 安装新技能需重启 |

---

## 📝 四、分步测试计划

### Phase 1: 启动与模式路由
> **目标**: 验证 3 种启动模式均能正常初始化

| 步骤 | 操作 | 预期结果 | 模式 |
|------|------|----------|------|
| 1.1 | `python -m src` | 进入 CLI 交互，显示欢迎信息 | CLI |
| 1.2 | `python -m src --standalone` | 跳过桥接检测，直接进入 CLI | CLI |
| 1.3 | `python -m src --no-bridge` | 同 --standalone | CLI |
| 1.4 | `python -m src --feishu` | 启动 WS 服务器，连接飞书 | 飞书 |
| 1.5 | `python -m src --unified` | CLI + 飞书同时运行 | 统一 |
| 1.6 | `python -m src --unified --feishu` | 显式启用飞书 | 统一 |
| 1.7 | 重复启动（不加 --force） | 提示已在运行，退出 | 全部 |
| 1.8 | `python -m src --force` | 强制获取实例锁启动 | 全部 |
| 1.9 | 缺少 .env 时启动 | 正常启动（dotenv 可选） | 全部 |
| 1.10 | 模型预设切换：`.profile precise` | 切换到 precise 预设 | CLI/统一 |

### Phase 2: 会话管理
> **目标**: 验证会话创建、切换、销毁、持久化

| 步骤 | 操作 | 预期结果 | 模式 |
|------|------|----------|------|
| 2.1 | `.sessions` | 显示当前会话列表 | CLI |
| 2.2 | `.session new test-001` | 创建新会话并切换 | CLI |
| 2.3 | 发送消息 "测试会话 001" | 正常回复，历史追加 | CLI |
| 2.4 | `.session new test-002` | 创建并切换到新会话 | CLI |
| 2.5 | `.session switch test-001` | 切回 test-001 | CLI |
| 2.6 | `.sessions` | 应显示 test-001 的历史消息 | CLI |
| 2.7 | 重启 → `.session switch test-001` | **历史应保留**（关键测试） | CLI |
| 2.8 | `.session destroy test-002` | 删除会话 | CLI |
| 2.9 | `.sessions` | test-002 不应出现 | CLI |
| 2.10 | 飞书发送消息 → CLI 查看 | 统一模式下可见飞书会话 | 统一 |
| 2.11 | CLI `.send <chat_id> hello` | 注入消息到飞书会话 | 统一 |
| 2.12 | 会话隔离验证：不同会话问相同问题 | 上下文不互相污染 | 全部 |

### Phase 3: Agent 核心功能
> **目标**: 验证规划、执行、工具调用链路

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 3.1 | 简单问答："今天星期几" | 使用 get_time 工具回答 |
| 3.2 | 文件操作："创建一个文件 hello.txt 写入 Hello" | 使用 write_file 创建 |
| 3.3 | 文件读取："读取 hello.txt" | 使用 read_file 读取 |
| 3.4 | 文件编辑："把 hello.txt 的 Hello 改成 World" | 使用 edit_file 替换 |
| 3.5 | 命令执行："执行 echo hello" | 使用 exec_command 执行 |
| 3.6 | 并行工具：同时需要 read_file + get_time | allow_parallel_tools 生效 |
| 3.7 | 多步任务："创建目录 a/b/c，在里面写入 test.txt" | 规划 → 顺序执行 |
| 3.8 | `.plan 分析当前项目结构` | 使用 planner 生成计划 |
| 3.9 | 工具失败场景："读取不存在的文件" | 优雅处理错误，不崩溃 |
| 3.10 | 超长对话（>20 轮） | max_turns 触发，正常结束 |

### Phase 4: 上下文压缩与记忆
> **目标**: 验证 token 估算、压缩、记忆检索

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 4.1 | 长对话后查看 token 报告 | 显示 token 使用量 |
| 4.2 | 超过 compress_threshold 的对话 | 自动压缩中间历史 |
| 4.3 | 压缩后继续对话 | 保留 system + 首条 + 最近对话 |
| 4.4 | 隔天问"我之前说过什么" | keyword_index 检索到历史 |
| 4.5 | `prune_expired` 调用 | 清理 30 天前的索引 |
| 4.6 | 中文+英文混合查询 | 两种分词均生效 |

### Phase 5: 循环检测
> **目标**: 验证三种循环检测模式

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 5.1 | 引导 agent 重复调用相同工具相同参数 ≥8 次 | warning 警告 |
| 5.2 | 继续到 ≥12 次 | critical 拦截 |
| 5.3 | 轮询模式：工具连续返回相同结果 | known_poll_no_progress 检测 |
| 5.4 | Ping-pong：A→B→A→B→A→B | ping_pong 检测 |
| 5.5 | 正常重试（参数不同） | 不触发检测 |

### Phase 6: 安全与沙箱
> **目标**: 验证路径隔离、命令过滤、进程管理

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 6.1 | "读取 C:\Windows\system32\config\sam" | 路径被限制在 workspace |
| 6.2 | "执行 rm -rf /" | 黑名单拦截 |
| 6.3 | 长时间运行的命令（sleep 60） | 超时终止 |
| 6.4 | 强制终止 agent | 清理所有追踪进程 |
| 6.5 | delete_file 工具 | require-confirm 需确认 |

### Phase 7: 技能系统
> **目标**: 验证技能发现、安装、管理

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 7.1 | `.skills` | 显示已加载技能 |
| 7.2 | `.skill search web` | 搜索本地 + ClawHub |
| 7.3 | 安装新技能后重启 | 自动加载新技能 |
| 7.4 | 技能贡献的工具 | 注册到全局 registry |
| 7.5 | `list_skills verbose` | 显示版本、作者、路径 |

### Phase 8: 飞书集成
> **目标**: 验证 WebSocket、消息处理、去重

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 8.1 | 飞书发送消息 | Agent 正常回复 |
| 8.2 | 快速连发 3 条消息 | 防抖合并处理 |
| 8.3 | 重复发送相同消息 | 去重，不重复处理 |
| 8.4 | 重启后发送消息 | dedup.json 生效 |
| 8.5 | 飞书服务器不可用 | CLI 模式正常降级 |
| 8.6 | 多聊天室同时发消息 | per-chat queue 顺序处理 |
| 8.7 | `.bridge` 查看状态 | 显示连接状态 |

### Phase 9: 统一模式
> **目标**: 验证 CLI + 飞书共享子系统

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 9.1 | 统一模式启动 | CLI + 飞书同时可用 |
| 9.2 | 飞书发消息 → CLI 终端显示思考 | ThinkingDisplay 带前缀 |
| 9.3 | CLI `.sessions` | 显示飞书会话 |
| 9.4 | CLI `.send <飞书chat_id> test` | 消息注入到飞书会话 |
| 9.5 | 同时处理 CLI + 飞书消息 | 不互相干扰 |
| 9.6 | **P0 Bug 验证**：飞书会话超过 40 轮 | 历史截断后 session_manager 仍同步更新 |

### Phase 10: 自我优化
> **目标**: 验证 self-opt 工具链

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 10.1 | `self_inspect` | 生成架构报告 |
| 10.2 | `generate_proposal` | 生成优化提案含风险等级 |
| 10.3 | `run_tests` | 执行 pytest，超时保护 |
| 10.4 | `git_snapshot create` | 创建 git commit |
| 10.5 | `git_snapshot list` | 显示最近 10 条历史 |
| 10.6 | `git_snapshot revert` | 回滚到指定 commit |

### Phase 11: 异常与边界情况
> **目标**: 验证鲁棒性

| 步骤 | 操作 | 预期结果 |
|------|------|----------|
| 11.1 | LLM API 超时 | 重试 2 次后报错 |
| 11.2 | 网络断开时启动 | 优雅降级 |
| 11.3 | 空输入 | 跳过，不处理 |
| 11.4 | 超长输入（>10KB） | 不崩溃 |
| 11.5 | 磁盘满 | 优雅处理持久化失败 |
| 11.6 | state/ 目录被删除 | 自动重建 |
| 11.7 | history.json 损坏 | 重建空历史 |
| 11.8 | Windows 编码问题 | UTF-8 正常输出 emoji |

---

## 🎯 五、本次修复清单

### ✅ P0 Bug #1: 统一模式历史引用断裂

**位置**: `src/unified.py` → `run_agent_with_thinking`

**问题代码**:
```python
if len(history) > 40:
    self._feishu_sessions[chat_id] = history[-40:]  # ← 创建新 list！
```

**修复后**:
```python
if len(history) > 40:
    # 原地截断，保持与 session_ctx.conversation_history 的引用一致
    del history[:len(history) - 40]
```

**理由**: `history` 是对 `session_ctx.conversation_history` 的引用。创建新 list 会断开与 session_ctx 的关联，导致 session_manager 中的历史不再更新。

### ✅ P1 Bug #4: exec_command 空命令检查

**位置**: `src/tools/exec.py` → `_exec_handler`

**修复内容**:
```python
command = str(args["command"]).strip()
if not command:
    return ToolResult(success=False, content="❌ 命令不能为空")
```

## 📊 六、测试优先级

| 优先级 | 阶段 | 理由 |
|--------|------|------|
| 🔴 P0 | Phase 2 + Phase 9 | 会话管理是核心功能，持久化失败 = 数据丢失 |
| 🔴 P0 | Bug #1 修复 | 统一模式历史断裂影响所有飞书长会话 |
| 🟡 P1 | Phase 3 + Phase 4 | Agent 核心功能，日常使用最频繁 |
| 🟡 P1 | Phase 8 | 飞书集成，依赖网络环境 |
| 🟢 P2 | Phase 5 + Phase 7 + Phase 10 | 进阶功能，使用频率较低 |
| 🟢 P2 | Phase 11 | 边界情况，低概率但需覆盖 |

---

## ✅ 七、总体评估

| 维度 | 评分 | 说明 |
|------|------|------|
| 架构完整性 | ⭐⭐⭐⭐ | 两阶段 Agent + 三种模式，设计清晰 |
| 代码质量 | ⭐⭐⭐⭐ | 注释完善，类型标注齐全 |
| 会话管理 | ⭐⭐⭐⭐ | standalone 已修复，统一模式有 P0 bug |
| 安全设计 | ⭐⭐⭐⭐ | 沙箱 + 黑名单 + 进程追踪 |
| 鲁棒性 | ⭐⭐⭐ | 多处 try/except，但缺少一些边界检查 |
| 可维护性 | ⭐⭐⭐⭐⭐ | 模块化清晰，文档完善 |

**结论**: 项目整体质量良好，核心架构稳固。修复 P0 Bug #1 后即可进入全面测试阶段。建议按 Phase 1→2→3→4→9 的顺序优先测试核心链路。
