---
name: web-tools
description: Web search (Tavily), browser extraction (Playwright), and lightweight URL fetching.
metadata: {"requires": {"env": ["TAVILY_API_KEY"]}}
---

# Web Tools

三个工具：`web_search`（Tavily 搜索）、`browser_extract_text`（Playwright 浏览器提取）、`fetch_url`（轻量 HTML 抓取）。

- `web_search` 需要 `TAVILY_API_KEY`
- `browser_extract_text` 需要 Playwright + chromium
