---
name: builtin-web
description: Web search, browser extraction, and URL fetching tools. Requires TAVILY_API_KEY for search and optional Playwright for browser extraction.
---

# Web Tools

提供联网搜索、浏览器页面提取和轻量网页抓取能力。

## 可用工具

| 工具 | 适用场景 | 特点 |
|------|---------|------|
| `web_search` | 搜索摘要、关键词、信息来源发现 | Tavily API 联网搜索，返回标题、链接、摘要；需要 `TAVILY_API_KEY` |
| `fetch_url` | URL 已知，需要从静态页面提取纯文本 | HTTP GET + HTML 去标签，轻量快速；不支持 JS 渲染 |
| `browser_extract_text` | 非公开内容或需要登录态，或已知静态层无效 | 无头 Chromium 打开页面并提取可见正文；需要 Playwright |

**工具选择原则**：
1. 需要搜索 → `web_search`
2. URL 已知且为静态页面 → `fetch_url`
3. 页面依赖前端渲染或需要登录态 → `browser_extract_text`
4. 时效性问题（天气、新闻等）→ 先 `get_time` 获取日期，再 `web_search`

## 依赖

- `web_search` 需要 `TAVILY_API_KEY` 环境变量
- `browser_extract_text` 需要 `pip install playwright && playwright install chromium`

## Tavily API 参数

`web_search` 支持以下参数：
- `query`（必需）：搜索关键词
- `maxResults`：返回结果数（默认 5，最多 20）
- `searchDepth`：`basic`（默认）或 `advanced`（更深入但更慢）
- `topic`：`general`（默认）或 `news`（新闻优先）
- `days`：仅新闻 topic 时有效，限制最近 N 天

## 浏览哲学

**像人一样搜索**：边看边判断，遇到阻碍就调整方向。

1. **定义成功标准**：先明确要获取什么信息，什么算完成
2. **选择起点**：根据任务性质选最可能直达的方式（搜索 vs 已知 URL）
3. **过程校验**：每一步都是证据，搜索没命中不等于"还没找对方法"，也可能是"目标不存在"
4. **完成判断**：确认获取到目标信息后停止，不要过度搜索

## 一手信息原则

搜索引擎是**定位**信息的工具，不可直接**证明**真伪。找到来源后，应直接访问原始页面读取原文。
- 政策/法规 → 发布机构官网
- 企业公告 → 公司官方新闻页
- 学术声明 → 原始论文/机构官网
- 工具能力/用法 → 官方文档、源码

## 使用示例

**天气查询**：
```
1. get_time() → 获取今天日期
2. web_search(query="深圳 2026年5月 天气预报") → 获取预报摘要
```

**新闻搜索**：
```
web_search(query="AI Agent 最新进展", maxResults=10, topic="news", days=7)
```

**页面内容提取**：
```
fetch_url(url="https://example.com/article")  # 静态页
browser_extract_text(url="https://dynamic-site.com/page")  # 需要 JS 渲染
```
