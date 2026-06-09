# Mini Agent Python 故障排查手册

> 版本: 2.1.0 | 最后更新: 2026-06-09

本手册提供常见问题的诊断方法和解决方案，帮助用户快速定位和解决问题。

---

## 目录

1. [启动问题](#启动问题)
2. [运行问题](#运行问题)
3. [飞书集成问题](#飞书集成问题)
4. [性能问题](#性能问题)
5. [配置问题](#配置问题)
6. [调试技巧](#调试技巧)

---

## 启动问题

### ❌ 导入错误：缺少依赖包

**症状**：
```
ModuleNotFoundError: No module named 'xxx'
```

**原因**：缺少必要的依赖包

**解决方案**：
1. 检查是否安装完整：
   ```bash
   pip install -e ".[dev,typing]"  # 开发环境
   pip install -e "."              # 生产环境
   ```

2. 如果使用飞书功能：
   ```bash
   pip install -e ".[feishu]"
   ```

3. 如果使用浏览器功能：
   ```bash
   pip install -e ".[browser]"
   ```

4. 如果使用 MCP 工具：
   ```bash
   pip install -e ".[mcp]"
   ```

**验证**：
```bash
python -c "import miniagent; print('导入成功')"
```

---

### ❌ 配置错误：config.user.json 格式问题

**症状**：
```
JSONDecodeError: Expecting property name enclosed in double quotes
```

**原因**：config.user.json 格式不正确

**解决方案**：
1. 检查 JSON 格式：
   ```bash
   python -m json.tool config.user.json
   ```

2. 验证配置有效性：
   ```bash
   python -m miniagent --doctor
   ```

3. 参考 config.defaults.json 格式：
   ```bash
   cp config.defaults.json config.user.json
   # 然后编辑 config.user.json，只修改需要覆盖的值
   ```

**最佳实践**：
- 使用 JSON 编辑器（避免手写格式错误）
- 只覆盖需要修改的配置项（其他保持默认）
- 添加注释说明配置用途

---

### ❌ 实例冲突：多实例运行

**症状**：
```
RuntimeError: 实例 ID X 已在运行（PID Y，心跳 Z 秒前）
```

**原因**：同一工作目录已有 Agent 运行

**解决方案**：
1. 检查运行实例：
   ```bash
   python -m miniagent --stop
   ```

2. 选择要停止的实例（交互式）：
   ```bash
   python -m miniagent --stop  # 选择 ID
   ```

3. 强制停止所有实例：
   ```bash
   python -m miniagent --stop --all
   ```

4. 或切换到不同工作目录：
   ```bash
   cd /path/to/other/project
   python -m miniagent
   ```

**预防措施**：
- 使用 `--session` 参数管理多个会话（而非多个实例）
- 定期清理僵尸实例（见 [ENGINEERING.md](ENGINEERING.md) §3.3）

---

## 运行问题

### ⚠️ Agent 无响应：队列阻塞

**症状**：输入后长时间无响应，无错误信息

**原因**：消息队列阻塞或 Agent 循环检测拦截

**诊断步骤**：
1. 查看队列状态：
   ```bash
   /queue status
   ```

2. 检查 Agent 状态：
   ```bash
   /status
   ```

3. 查看循环检测日志：
   ```bash
   grep "LoopDetector" logs/agent.log | tail -10
   ```

**解决方案**：
1. 如果队列阻塞：
   ```bash
   /queue abort  # 中止当前任务
   ```

2. 如果循环检测拦截：
   - 检查是否输入重复内容
   - 修改 prompt 使其更具体
   - 调整循环检测阈值（见 `config.defaults.json`）

3. 如果进程卡死：
   ```bash
   Ctrl+C  # 中断
   python -m miniagent --stop --all  # 清理
   ```

---

### ⚠️ 工具执行失败：权限问题

**症状**：
```
PermissionError: [Errno 13] Permission denied
```

**原因**：工具执行超出沙箱限制

**诊断步骤**：
1. 检查沙箱配置：
   ```bash
   /config security
   ```

2. 查看默认工作区：
   ```bash
   python -c "from miniagent.security.sandbox import get_default_workspace; print(get_default_workspace())"
   ```

**解决方案**：
1. 检查路径是否在允许列表：
   - 默认工作区：`{cwd}/workspaces/`
   - 添加路径到 `allowed_paths`（见 `config.defaults.json`）

2. 调整沙箱策略：
   ```json
   {
     "security": {
       "sandbox_enabled": false  // 关闭沙箱（仅开发环境）
     }
   }
   ```

**安全提醒**：
- 生产环境必须启用沙箱
- 只添加必要的路径到 `allowed_paths`
- 避免添加系统关键路径

---

### ⚠️ 工具执行超时

**症状**：
```
TimeoutError: Tool execution timeout after X seconds
```

**原因**：工具执行时间超过限制

**解决方案**：
1. 调整超时时间：
   ```json
   {
     "agent": {
       "tool_call_timeout": 60  // 默认 30 秒，增加到 60 秒
     }
   }
   ```

2. 检查工具实现：
   - 是否有网络延迟问题
   - 是否处理大量数据
   - 是否有死循环

3. 优化工具性能：
   - 使用异步处理
   - 分批处理大数据
   - 添加进度反馈

---

## 飞书集成问题

### ⚠️ 飞书无响应：连接失败

**症状**：飞书消息发送后无响应，WebSocket 无日志

**原因**：飞书凭证错误或网络问题

**诊断步骤**：
1. 检查飞书状态：
   ```bash
   /feishu status
   ```

2. 查看飞书日志：
   ```bash
   grep "FeishuRuntime" logs/agent.log | tail -20
   ```

3. 检查凭证配置：
   ```bash
   /config feishu
   ```

**解决方案**：
1. 验证飞书凭证：
   - `FEISHU_APP_ID` 是否正确
   - `FEISHU_APP_SECRET` 是否正确
   - 应用是否发布到飞书

2. 检查网络连接：
   ```bash
   ping open.feishu.cn
   ```

3. 检查事件订阅：
   - 飞书开放平台 → 应用 → 事件订阅
   - 是否订阅了 `im.message.receive_v1`

4. 检查入站锁：
   ```bash
   cat workspaces/feishu_inbound_owner.json
   ```

---

### ⚠️ 飞书卡片显示异常

**症状**：飞书卡片内容格式错误、表格显示不全

**原因**：飞书客户端对 Markdown 支持限制

**解决方案**：
1. 检查卡片长度：
   - 单张卡片不超过 48k 字符（见 `feishu.card.body_max_chars`）
   - 超长内容自动分片

2. 表格显示问题：
   ```json
   {
     "feishu": {
       "markdown_commands": false  // 关闭 Markdown 表格
     }
   }
   ```

3. 使用本地 CLI 验证：
   ```bash
   /session switch <飞书会话ID>
   ```

---

## 性能问题

### ⚠️ 内存占用过高

**症状**：进程内存持续增长，占用超过 1GB

**原因**：历史过长、记忆膨胀、缓存未清理

**诊断步骤**：
1. 查看历史长度：
   ```bash
   /session list
   ```

2. 检查会话历史文件：
   ```bash
   ls -lh workspaces/sessions/*/history.json
   ```

3. 查看记忆文件：
   ```bash
   ls -lh workspaces/memory/*.md
   ```

**解决方案**：
1. 清理旧会话：
   ```bash
   /session delete <旧会话ID>
   ```

2. 调整历史保留：
   ```json
   {
     "memory": {
       "history_tail_messages": 100  // 从 200 减少到 100
     }
   }
   ```

3. 启用历史归档：
   ```json
   {
     "memory": {
       "archive_enabled": true,
       "archive_after_days": 30
     }
   }
   ```

4. 定期清理：
   ```bash
   python scripts/cleanup_old_sessions.py --days 90
   ```

---

### ⚠️ 响应缓慢：工具超时

**症状**：Agent 响应时间超过 30 秒

**原因**：工具执行慢、网络延迟、模型响应慢

**诊断步骤**：
1. 查看工具调用统计：
   ```bash
   /stats
   ```

2. 检查网络延迟：
   ```bash
   ping api.openai.com
   ```

3. 查看模型响应时间：
   ```bash
   grep "LLM call" logs/agent.log | grep "duration"
   ```

**解决方案**：
1. 优化工具性能：
   - 减少工具调用次数
   - 使用并行工具调用（见 `allow_parallel_tools`）

2. 调整模型参数：
   ```json
   {
     "model": {
       "model": "gpt-4o-mini"  // 使用更快的模型
     }
   }
   ```

3. 启用流式处理：
   ```json
   {
     "agent": {
       "streaming": true
     }
   }
   ```

---

## 配置问题

### ⚠️ 配置不生效

**症状**：修改配置后行为未改变

**原因**：配置未加载或配置层级问题

**解决方案**：
1. 确认配置已加载：
   ```bash
   /reload-config
   ```

2. 检查配置优先级：
   - User 层（`config.user.json`）覆盖 Defaults 层
   - Internal 层不可修改（见 `_config_guide`）

3. 查看实际生效配置：
   ```bash
   /config <section>
   ```

4. 检查配置格式：
   ```bash
   python -m json.tool config.user.json
   ```

---

### ⚠️ 时区设置不生效

**症状**：定时任务时间与预期不符

**原因**：时区配置不一致

**解决方案**：
1. 检查时区配置：
   ```bash
   /config timezone
   ```

2. 设置系统时区：
   ```bash
   export TZ="Asia/Shanghai"
   ```

3. 或在配置中设置：
   ```json
   {
     "timezone": {
       "default": "Asia/Shanghai"
     }
   }
   ```

4. 对齐定时任务时区：
   ```bash
   /schedule align-tz
   ```

---

## 调试技巧

### 🔧 日志级别设置

**开启详细日志**：
```bash
export AGENT_DEBUG=1
python -m miniagent
```

**查看日志文件**：
```bash
tail -f logs/agent.log
```

**日志位置**：
- `logs/agent.log` - Agent 主日志
- `logs/activity.jsonl` - 活动日志（JSON格式）
- `workspaces/sessions/*/history.json` - 会话历史

---

### 🔧 断点调试方法

**使用 Python 调试器**：
```bash
python -m pdb -m miniagent
```

**关键断点位置**：
- `miniagent/core/executor.py:execute_plan` - 执行入口
- `miniagent/engine/main.py:run_cli_loop` - 主循环
- `miniagent/tools/exec.py:exec_command` - 工具执行

**调试工具调用**：
```python
# 在工具中添加调试日志
import logging
_logger = logging.getLogger(__name__)
_logger.debug(f"Tool called with args: {args}")
```

---

### 🔧 性能分析工具

**使用 tracemalloc**：
```bash
python scripts/perf_profile_tracemalloc.py
```

**查看内存热点**：
```bash
python -c "
import tracemalloc
tracemalloc.start()
# ... 运行 Agent ...
snapshot = tracemalloc.take_snapshot()
top_stats = snapshot.statistics('lineno')
for stat in top_stats[:10]:
    print(stat)
"
```

**使用 pytest 性能测试**：
```bash
pytest -m perf --cov-report=term-missing
```

---

## 常见错误代码速查

| 错误代码 | 含义 | 解决方案 |
|---------|------|---------|
| `E001` | 导入错误 | 安装缺失依赖 |
| `E002` | 配置错误 | 检查 JSON 格式 |
| `E003` | 实例冲突 | 停止冲突实例 |
| `E004` | 队列阻塞 | 中止当前任务 |
| `E005` | 权限问题 | 调整沙箱配置 |
| `E006` | 工具超时 | 增加超时时间 |
| `E007` | 飞书连接失败 | 检查凭证和网络 |
| `E008` | 内存过高 | 清理历史和记忆 |
| `E009` | 响应缓慢 | 优化工具和模型 |
| `E010` | 时区错误 | 设置正确时区 |

---

## 获取更多帮助

1. **查阅文档**：
   - [USER_GUIDE.md](USER_GUIDE.md) - 用户指南
   - [ARCHITECTURE.md](ARCHITECTURE.md) - 架构说明
   - [ENGINEERING.md](ENGINEERING.md) - 工程指南

2. **运行诊断**：
   ```bash
   python -m miniagent --doctor
   ```

3. **查看状态**：
   ```bash
   /status
   /config
   /stats
   ```

4. **提交问题**：
   - GitHub Issues: https://github.com/yingzhudashu/miniagent-python/issues
   - 提供错误日志和配置信息

---

**文档维护**：本手册会持续更新，如有新问题请提交 Issue 或更新文档。