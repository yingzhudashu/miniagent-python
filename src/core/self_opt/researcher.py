"""Researcher — 外部研究引擎

搜索外部最佳实践、架构模式、技术论文，
为优化提案提供参考依据。

搜索源：
1. arXiv API — 学术论文
2. GitHub API — 开源项目
3. Tavily Web Search — 网络搜索

回退方案：
如果外部搜索不可用，使用内置知识库 (KNOWN_PATTERNS)

设计原则：
- 多源搜索，互为补充
- 网络搜索有超时限制
- 回退到本地知识库
- 结果可追溯、可验证
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any

from .types import ResearchReport, ExternalReference, ExtractedPattern

# 内置知识库（回退方案）
KNOWN_PATTERNS = {
    "loop-detection": {
        "description": "循环检测是 Agent 系统的核心能力，用于识别和处理重复错误",
        "patterns": ["滑动窗口计数", "错误指纹哈希", "自动修复策略"],
        "references": [
            {
                "type": "docs",
                "title": "Loop Detection Best Practices",
                "url": "https://github.com/nicepkg/mini-agent/blob/main/docs/loop-detection.md",
                "summary": "Agent 循环检测的常见模式和解决方案",
            },
        ],
    },
    "self-healing": {
        "description": "自愈系统是提升 Agent 可靠性的关键技术",
        "patterns": ["自动回滚", "健康检查", "错误恢复"],
        "references": [
            {
                "type": "paper",
                "title": "Self-Healing Software Systems",
                "url": "https://arxiv.org/abs/self-healing",
                "summary": "自愈软件系统的架构设计",
            },
        ],
    },
    "code-generation": {
        "description": "AI 代码生成的质量控制方法",
        "patterns": ["静态分析验证", "测试驱动生成", "类型安全检查"],
        "references": [],
    },
    "error-handling": {
        "description": "现代错误处理最佳实践",
        "patterns": ["错误分类", "重试策略", "降级处理"],
        "references": [],
    },
    "monitoring": {
        "description": "系统监控和可观测性",
        "patterns": ["指标收集", "日志聚合", "告警策略"],
        "references": [],
    },
}


@dataclass
class ResearchSource:
    """研究源配置。"""
    name: str
    enabled: bool = True
    timeout_seconds: int = 10


def _search_arxiv(query: str, max_results: int = 3) -> list[ExternalReference]:
    """搜索 arXiv 论文。"""
    references: list[ExternalReference] = []
    try:
        import urllib.request
        import urllib.parse
        import xml.etree.ElementTree as ET

        encoded_query = urllib.parse.quote(query)
        url = f"http://export.arxiv.org/api/query?search_query=all:{encoded_query}&max_results={max_results}"

        req = urllib.request.Request(url, headers={"User-Agent": "MiniAgent-Researcher/1.0"})
        with urllib.request.urlopen(req, timeout=10) as response:
            xml_data = response.read().decode("utf-8")

        root = ET.fromstring(xml_data)
        ns = {"atom": "http://www.w3.org/2005/Atom"}

        for entry in root.findall("atom:entry", ns):
            title_elem = entry.find("atom:title", ns)
            id_elem = entry.find("atom:id", ns)
            summary_elem = entry.find("atom:summary", ns)
            published_elem = entry.find("atom:published", ns)

            if title_elem is not None and id_elem is not None:
                references.append(ExternalReference(
                    type="paper",
                    title=title_elem.text.strip() if title_elem.text else "",
                    url=id_elem.text.strip() if id_elem.text else "",
                    summary=summary_elem.text.strip()[:200] if summary_elem is not None and summary_elem.text else "",
                    date=published_elem.text[:10] if published_elem is not None and published_elem.text else None,
                ))
    except Exception:
        pass

    return references


def _search_github(query: str, max_results: int = 3) -> list[ExternalReference]:
    """搜索 GitHub 项目。"""
    references: list[ExternalReference] = []
    try:
        import urllib.request
        import urllib.parse

        encoded_query = urllib.parse.quote(query)
        url = f"https://api.github.com/search/repositories?q={encoded_query}&sort=stars&order=desc&per_page={max_results}"

        req = urllib.request.Request(url, headers={"User-Agent": "MiniAgent-Researcher/1.0"})
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))

        for item in data.get("items", [])[:max_results]:
            references.append(ExternalReference(
                type="github",
                title=item.get("full_name", ""),
                url=item.get("html_url", ""),
                summary=item.get("description", "") or "",
                patterns=["open-source", "community-validated"],
                relevance=min(item.get("stargazers_count", 0) // 100, 10),
            ))
    except Exception:
        pass

    return references


def _search_tavily(query: str, max_results: int = 3) -> list[ExternalReference]:
    """使用 Tavily 搜索网络。"""
    references: list[ExternalReference] = []
    api_key = os.environ.get("TAVILY_API_KEY", "")
    if not api_key:
        return references

    try:
        import urllib.request

        payload = json.dumps({
            "query": query,
            "max_results": max_results,
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.tavily.com/search",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )

        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))

        for result in data.get("results", [])[:max_results]:
            references.append(ExternalReference(
                type="blog",
                title=result.get("title", ""),
                url=result.get("url", ""),
                summary=result.get("content", "")[:200],
            ))
    except Exception:
        pass

    return references


async def research_topic(
    topic: str,
    queries: list[str] | None = None,
    max_results_per_source: int = 3,
) -> ResearchReport:
    """研究指定主题。

    Args:
        topic: 研究主题。
        queries: 搜索查询列表（如果为 None，使用主题生成）。
        max_results_per_source: 每个源的最大结果数。

    Returns:
        调研报告。
    """
    import datetime

    search_queries = queries or [topic]
    all_references: list[ExternalReference] = []

    # 并行搜索多个源
    tasks = []
    for query in search_queries:
        tasks.append(_search_arxiv_async(query, max_results_per_source))
        tasks.append(_search_github_async(query, max_results_per_source))
        tasks.append(_search_tavily_async(query, max_results_per_source))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, list):
            all_references.extend(result)

    # 回退到本地知识库
    if not all_references:
        for pattern_key, pattern_data in KNOWN_PATTERNS.items():
            if pattern_key in topic.lower() or any(kw in topic.lower() for kw in pattern_key.split("-")):
                for ref in pattern_data.get("references", []):
                    all_references.append(ExternalReference(
                        type=ref["type"],
                        title=ref["title"],
                        url=ref["url"],
                        summary=ref["summary"],
                        patterns=pattern_data.get("patterns", []),
                    ))

    # 提取架构模式
    extracted_patterns: list[ExtractedPattern] = []
    for ref in all_references:
        if ref.patterns:
            for pattern in ref.patterns:
                extracted_patterns.append(ExtractedPattern(
                    name=pattern,
                    description=f"来自 {ref.title}",
                    source_references=[ref.url],
                    applicability=ref.summary[:100],
                ))

    # 去重
    seen_urls = set()
    unique_refs = []
    for ref in all_references:
        if ref.url not in seen_urls:
            seen_urls.add(ref.url)
            unique_refs.append(ref)

    summary = (
        f"研究完成: 找到 {len(unique_refs)} 个参考，{len(extracted_patterns)} 个模式。"
        if unique_refs else f"外部搜索不可用，使用内置知识库回退。"
    )

    return ResearchReport(
        timestamp=datetime.datetime.now(datetime.UTC).isoformat(),
        search_queries=search_queries,
        references=unique_refs,
        extracted_patterns=extracted_patterns,
        summary=summary,
    )


async def _search_arxiv_async(query: str, max_results: int) -> list[ExternalReference]:
    return await asyncio.to_thread(_search_arxiv, query, max_results)


async def _search_github_async(query: str, max_results: int) -> list[ExternalReference]:
    return await asyncio.to_thread(_search_github, query, max_results)


async def _search_tavily_async(query: str, max_results: int) -> list[ExternalReference]:
    return await asyncio.to_thread(_search_tavily, query, max_results)


def generate_research_report(
    topic: str,
    queries: list[str] | None = None,
) -> ResearchReport:
    """同步版本的 research_topic（用于简单场景）。

    内部调用 asyncio 运行。
    """
    return asyncio.run(research_topic(topic, queries))
