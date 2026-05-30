---
name: web-tools
description: Web search (Tavily), browser extraction (Playwright), URL fetching, and HTTP file download.
metadata: {"requires": {"env": ["TAVILY_API_KEY"], "optional": ["playwright"]}}
---

# Web Tools

四个工具：`web_search`（Tavily 搜索）、`browser_extract_text`（Playwright 浏览器提取）、`fetch_url`（轻量 HTML 抓取）、`download_file`（HTTP 文件下载）。

## 工具列表

| 工具 | 功能 | 依赖 |
|------|------|------|
| `web_search` | Tavily 联网搜索 | `TAVILY_API_KEY` |
| `fetch_url` | 抓取静态 HTML 并提取文本 | 无 |
| `download_file` | 下载 HTTP 文件到沙箱 | httpx 或 urllib |
| `browser_extract_text` | 无头 Chromium 提取页面正文 | Playwright + chromium |

## 使用建议

- 需要搜索 → `web_search`
- URL 已知静态页 → `fetch_url`
- 需要下载文件 → `download_file`
- 需要 JS 渲染或登录态 → `browser_extract_text`

## 依赖安装

```bash
# 搜索（必需）
export TAVILY_API_KEY="your-api-key"

# 浏览器提取（可选）
pip install playwright
playwright install chromium
```