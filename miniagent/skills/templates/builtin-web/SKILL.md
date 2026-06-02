---
name: builtin-web
description:
  所有联网操作的核心技能，提供搜索、网页抓取、文件下载、浏览器提取能力。
  触发场景：用户要求搜索信息、查看网页内容、下载文件、获取需要渲染的页面内容等。
---

# Web Tools

提供联网搜索、网页抓取、文件下载和浏览器页面提取能力。

## 可用工具

| 工具 | 适用场景 | 特点 |
|------|---------|------|
| `web_search` | 搜索摘要、关键词、信息来源发现 | Tavily API 联网搜索，返回标题、链接、摘要；需要 `TAVILY_API_KEY` |
| `fetch_url` | URL 已知，需要从静态页面提取纯文本 | HTTP GET + HTML 去标签，轻量快速；不支持 JS 渲染 |
| `download_file` | 下载 PDF、ZIP、图片、视频等二进制文件 | HTTP 流式下载，支持大小限制，保存到沙箱目录 |
| `browser_extract_text` | 非公开内容或需要登录态，或已知静态层无效 | 无头 Chromium 打开页面并提取可见正文；需要 Playwright |

**工具选择原则**：

1. 需要搜索 → `web_search`
2. URL 已知且为静态页面 → `fetch_url`
3. 需要下载文件 → `download_file`
4. 页面依赖前端渲染或需要登录态 → `browser_extract_text`
5. 时效性问题（天气、新闻等）→ 先 `get_time` 获取日期，再 `web_search`

## 依赖

- `web_search` 需要 `TAVILY_API_KEY` 环境变量
- `browser_extract_text` 需要 `pip install playwright && playwright install chromium`
- `download_file` 无额外依赖（使用 httpx 或 urllib）

## 浏览哲学

**像人一样思考，兼顾高效与适应性的完成任务。**

执行任务时不会过度依赖固有印象所规划的步骤，而是带着目标进入，边看边判断，遇到阻碍就解决，发现内容不够就深入——全程围绕「我要达成什么」做决策。

**① 拿到请求** — 先明确用户要做什么，定义成功标准：什么算完成了？需要获取什么信息、执行什么操作、达到什么结果？

**② 选择起点** — 根据任务性质、平台特征、达成条件，选一个最可能直达的方式作为第一步去验证。一次成功当然最好；不成功则在③中调整。

**③ 过程校验** — 每一步的结果都是证据，不只是成功或失败的二元信号。用结果对照①的成功标准：
- 搜索没命中不等于"还没找对方法"，也可能是"目标不存在"
- API 报错、页面缺少预期元素、重试无改善，都是在告诉你该重新评估方向
- 遇到弹窗、登录墙等障碍，判断它是否真的挡住了目标

**④ 完成判断** — 对照定义的任务成功标准，确认任务完成后才停止，但也不要过度操作。

## 一手信息原则

**确保信息的真实性，一手信息优于二手信息**：搜索引擎和聚合平台是信息发现入口，是**定位**信息的工具，不可直接**证明**真伪。找到来源后，直接访问读取原文。

| 信息类型 | 一手来源 |
|----------|---------|
| 政策/法规 | 发布机构官网 |
| 企业公告 | 公司官方新闻页 |
| 学术声明 | 原始论文/机构官网 |
| 工具能力/用法 | 官方文档、源码 |

**找不到官网时**：权威媒体的原创报道（非转载）可作为次级依据，但需向用户说明来源限制。

## 工具详细参数

### web_search

```python
web_search(
    query: str,           # 搜索关键词（必需）
    maxResults: int = 8,  # 返回结果数（默认 8，最多 20）
    searchDepth: str,     # 'basic' 或 'advanced'
    topic: str,           # 'general' 或 'news'
    days: int,            # 仅 news topic，限制最近 N 天
)
```

### fetch_url

```python
fetch_url(
    url: str,             # HTTP/HTTPS 网址（必需）
    maxChars: int = 5000, # 最大返回字符数
)
```

### download_file

```python
download_file(
    url: str,             # HTTP/HTTPS 文件 URL（必需）
    filename: str,        # 保存的文件名（可选，默认从 URL 提取）
    max_size_mb: int = 50, # 最大下载大小限制（MB，默认 50，最大 500）
)
```

返回：
- 文件路径（沙箱目录内）
- 文件大小
- MIME 类型

### browser_extract_text

```python
browser_extract_text(
    url: str,                      # HTTP/HTTPS 页面 URL（必需）
    maxChars: int = 12000,         # 最大返回字符数
    waitUntil: str,                # 'load' / 'domcontentloaded' / 'networkidle'
)
```

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

**文件下载**：
```
download_file(url="https://example.com/report.pdf")  # 下载 PDF
download_file(url="https://github.com/user/repo/archive/main.zip", filename="repo.zip")  # 下载 ZIP
```

## 安全限制

- 所有下载文件保存到沙箱目录（会话 `files/` 目录）
- 禁止路径穿越（文件名必须是简单的 basename）
- 文件大小限制：默认 50MB，最大 500MB
- 仅支持 HTTP/HTTPS 协议

## CDP 模式（可选高级功能）

对于需要登录态、复杂交互操作的场景，可以考虑使用 CDP (Chrome DevTools Protocol) 模式直连用户 Chrome。但这需要：
1. Node.js 22+
2. 用户 Chrome 开启远程调试
3. 运行 CDP Proxy 脚本

CDP 模式的详细使用方法见 web-access skill 的参考文档。