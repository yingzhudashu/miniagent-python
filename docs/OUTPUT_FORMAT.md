# 输出格式规范

> Mini Agent Python | CLI 与飞书通道的输出格式、流式输出、间距规则

## 概述

本文档说明 Agent 从输入到输出的完整格式规范，涵盖 CLI 全屏模式、CLI 回退模式、飞书卡片模式三种渲染路径。

## 1. CLI 全屏模式（prompt_toolkit TUI）

### 1.1 轮次结构

每轮对话由以下区块组成，区块间通过分隔线和空行区分：

```
════════════════════════════════════════  ← 粗分隔线（上一轮结束标记）



════════════════════════════════════════  ← 粗分隔线（本轮开始标记）
You
────────────────────────────────────────  ← 细分隔线
用户输入内容

💡 [0] [需求澄清]
  需求澄清思考内容

💡 [1] [评估与计划]
  难度评估与计划生成内容

💡 [2] [执行]
  执行阶段流式内容
  🔧 read_file — 读取文件
  🔧 write_file — 写入文件

💡 [3] [反思评估]
  反思评估内容

────────────────────────────────────────  ← 细分隔线
Assistant
────────────────────────────────────────
最终回复内容（支持 Markdown 渲染）

════════════════════════════════════════  ← 粗分隔线（本轮结束）
```

### 1.2 Markdown 渲染宽度和对齐

**宽度计算**：Assistant 回复和思考正文的 Markdown 渲染宽度基于终端视口宽度：
- 使用 `viewport_width - 4` 作为渲染宽度（而非早期的 `viewport_width // 3`）
- 最小宽度 40 列，最大宽度 500 列（适应宽屏显示器）
- 确保表格和长内容有足够的显示空间

**标题对齐**：
- Markdown 标题（`#`、`##` 等）默认靠左对齐，左侧有适当间距
- 不使用居中对齐，便于阅读和层次区分

**环境变量控制**：
- `MINIAGENT_CLI_RAW_MARKDOWN=1`：关闭 Rich 渲染，显示原始 Markdown
- `MINIAGENT_CLI_THINKING_RICH=1`：对非流式思考正文使用 Rich 渲染

### 1.3 水平滚动与折行策略

**动态折行**：
- 终端宽度 ≥ 60 列：自动折行（`wrap_lines=True`）
- 终端宽度 < 60 列：禁用折行，启用水平滚动

**水平滚动控制**：
- **键盘**：`Shift+Left` / `Shift+Right` 每步滚动约 10 字符
- **鼠标**：在非折行模式下，拖动内容区域可水平滚动
- **自动重置**：终端宽度恢复到 ≥ 60 列时，自动重置水平滚动位置并恢复折行

### 1.4 垂直滚动条交互

**滚动条功能**：
- 垂直滚动条始终显示在输出区右侧（约 1-2 列宽度）
- **滚轮**：向上/向下滚动约视口高度的 1/6
- **键盘**：`PageUp` / `PageDown` 滚动约半屏

**滚动条点击/拖动**：
- 点击滚动条区域：直接跳转到对应位置
- 拖动滚动条：平滑滚动，可连续拖动

### 1.5 间距规则

| 位置 | 空白行数 | 实现 |
|------|----------|------|
| 轮次之间（上一轮 reply 后 → 下一轮 You 前） | **3 行**（2 行可见空白 + 1 行粗分隔线） | `_cli_block_user` 写入 `"\n\n\n"` |
| 思考阶段之间（如 `[需求澄清]` → `[评估与计划]`） | **2 行**（1 行可见空白） | `ThinkingDisplay.show()` 阶段切换 emit `"\n\n"` |
| 思考结束 → 最终回复前 | 由 `_cli_block_reply` 的 `"\n"` + light rule 处理 | `_cli_block_reply` |

### 1.6 思考步骤编号

- 每轮对话开始时，通过 `ThinkingDisplay.reset_counter(session_key)` 重置为 **0**
- 每个思考阶段（含流式和非流式）分配独立编号 `[n]`
- CLI 和飞书共用同一个 `session_key`，确保计数器一致

## 2. CLI 回退模式（`input()` 循环）

当 prompt_toolkit 不可用时（非 TTY 环境、测试子进程等），使用简易 `print()` 循环：

```
════════════════════════════════════  ← _fb_rule_heavy()
You
────────────────────────────────────  ← _fb_rule_light()
用户输入

（思考过程直接打印到 stdout）

────────────────────────────────────  ← _fb_rule_light()
Assistant
────────────────────────────────────
最终回复

（两个 print() 产生轮次间空白）
```

**轮次间空白**：`_process_input` 开头连续两个 `print()` 产生两行空白。

## 3. 飞书卡片模式

### 3.1 思考卡片

- 每个思考阶段通过 `push_feishu_thinking_stream` 发送独立的**交互式卡片**
- 使用 `new_round=True` 时新建卡片，`new_round=False` 时 PATCH 更新现有卡片
- 阶段切换时通过 `finalize_only=True` 收尾当前卡片，再创建新卡片
- 飞书 UI 自动在消息间提供视觉间距

### 3.2 节流策略

| 参数 | 默认 | 说明 |
|------|------|------|
| `FEISHU_THINKING_PATCH_INTERVAL_S` | 0.5s | PATCH 最小时间间隔 |
| `FEISHU_THINKING_PATCH_CHAR_DELTA` | 50 chars | PATCH 最小字符增量 |
| `FEISHU_THINKING_PATCH_BUDGET` | 20 | 单阶段最大 PATCH 次数 |

### 3.3 工具意图合并

当 `MINIAGENT_THINKING_MERGE_TOOLS=1`（默认开启）时，同一思考阶段内的工具调用意图行（🔧）合并到当前卡片，不新建独立消息：

```
[评估与计划]
规划完成：xxx

**工具**

- 🔧 read_file — 读取配置文件
- 🔧 write_file — 写入输出文件
```

### 3.4 跨通道隔离

CLI 与飞书共进程时，通过 `UnifiedEngine._exec_lock`（`asyncio.Lock`）确保同一时刻只有一个通道在处理 Agent 请求，避免输出交叉。

## 4. 流式输出机制

### 4.1 全流式原则

**所有思考阶段均使用 `streaming=True`**，确保：
- CLI 终端实时看到增量输出
- 飞书卡片通过 PATCH 逐步更新
- 阶段间通过 header 切换自动触发 phase_changed 逻辑

### 4.2 Phase Changed 逻辑

当 `ThinkingDisplay.show()` 检测到 header 变化时：

1. **飞书侧**：调用 `finalize_feishu_thinking_stream()` 收尾当前卡片
2. **CLI 侧**：emit `"\n\n"` 结束上一阶段
3. 重置 `stream_step`、`stream_header`、`stream_printed` 状态
4. 新阶段以新 header 开始

### 4.3 相关环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `MINIAGENT_THINKING_MERGE_TOOLS` | `1` | 同阶段工具意图行是否合并到思考卡片 |
| `MINIAGENT_CLI_THINKING_RICH` | 关 | 是否对思考正文使用 Rich→ANSI 渲染 |
| `MINIAGENT_ANNOUNCE_DIFFICULTY_AND_PLAN` | `1` | 是否推送难度评估与计划到思考流 |

## 5. 会话历史中的思考块

会话 `history.json` 中，每轮对话的 thinking 部分按以下顺序拼接（`\n\n` 分隔）：

1. `[步骤 i/n]` 规划步骤（按 step_number 排序）
2. `[评估与计划]` 难度评估与计划
3. `[执行]` 或 `[步骤 i/n]` 执行阶段
4. `第 n 轮` ReAct 轮次内容
5. 工具意图行

排序键见 `engine.py` 的 `_turn_label_sort_key`。

## 相关文档

- [ARCHITECTURE.md](ARCHITECTURE.md) — 系统架构
- [CLI.md](CLI.md) — CLI 使用指南
- [FEISHU.md](FEISHU.md) — 飞书配置与使用
