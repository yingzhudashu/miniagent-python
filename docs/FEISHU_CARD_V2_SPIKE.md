# 飞书交互卡片 JSON v2（table / column_set）调研备忘

## 现状

- 文本 Agent 回复与思考卡片经 [`miniagent/feishu/poll_server.py`](../miniagent/feishu/poll_server.py) 发送 `msg_type=interactive`，正文为旧式结构中的 `elements[].tag=div` + `text.tag=lark_md`。
- 宽 GFM 表在列数超阈值时降级为提示 + 代码块内等宽文本表（`MINIAGENT_FEISHU_TABLE_FALLBACK`），不依赖 v2。

## v2 能力（开放平台）

- 卡片 JSON 2.0 提供 `column_set`、`table` 等容器，适合结构化表格与分栏（需客户端版本支持，见开放平台「component JSON v2.0」文档）。
- **与当前路径的关系**：v2 与现有 `lark_md` 单 `div` 混排在同一条 `interactive` 消息内的兼容性需按官方 schema 实测；整体升级为 v2 卡片或「第二张卡片专发 table」会涉及较大改造与回退策略。

## 建议

- 短期：继续优化 `lark_md` + 文本表降级（已实现）。
- 中长期：若需「真表格」预览，单独立项：解析 GFM → 生成 v2 `table`/`column_set`，限定行列上限，失败时回退 `lark_md`。
