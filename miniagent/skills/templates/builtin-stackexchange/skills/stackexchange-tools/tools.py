"""Structured Stack Exchange search for practical troubleshooting evidence."""

from __future__ import annotations

import asyncio
import html
import math
import os
import re
import time
from collections import OrderedDict
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

from miniagent.infrastructure.httpx_pool import get_shared_httpx_client
from miniagent.types.error_prefix import ERROR_PREFIX
from miniagent.types.tool import ToolContext, ToolDefinition, ToolResult

_API_ROOT = "https://api.stackexchange.com/2.3"
_CACHE_TTL_SECONDS = 15 * 60
_CACHE_MAX_ITEMS = 128
_MAX_SITES = 3
_MAX_RESULTS_PER_SITE = 5
_MAX_OUTPUT_CHARS = 16_000
_QUESTION_EXCERPT_CHARS = 600
_ANSWER_EXCERPT_CHARS = 1_400
_SITE_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$")

_cache: OrderedDict[str, tuple[float, str, dict[str, Any]]] = OrderedDict()
_backoff_until: dict[str, float] = {}


class _StackExchangeError(RuntimeError):
    """A concise API error safe to return to the model."""


class _TextExtractor(HTMLParser):
    """Convert post HTML to readable text while preserving code boundaries."""

    _BLOCK_TAGS = {
        "address", "blockquote", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6",
        "hr", "li", "ol", "p", "pre", "table", "tr", "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.in_pre = False
        self.in_code = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag == "pre":
            self.in_pre = True
            self.parts.append("\n```\n")
        elif tag == "code" and not self.in_pre:
            self.in_code = True
            self.parts.append("`")
        elif tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "pre":
            self.in_pre = False
            self.parts.append("\n```\n")
        elif tag == "code" and self.in_code and not self.in_pre:
            self.in_code = False
            self.parts.append("`")
        elif tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _html_to_text(value: Any) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(str(value or ""))
        parser.close()
        text = "".join(parser.parts)
    except Exception:
        text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = html.unescape(text).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


_SENSITIVE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{12,}"), "[credential]"),
    (re.compile(r"\b(?:sk|tvly|ghp|github_pat)-?[A-Za-z0-9_]{12,}\b", re.I), "[credential]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), "[credential]"),
    (re.compile(r"(?i)\b(?:api[_-]?key|password|passwd|secret|token)\s*[:=]\s*\S+"), "[credential]"),
    (re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I), "[email]"),
    (re.compile(r"(?<!\w)[A-Za-z]:\\(?:[^\s\\]+\\)*[^\s]*"), "[local-path]"),
    (re.compile(r"(?<![:\w])/(?:home|Users|private|var|opt|srv|mnt|tmp)/[^\s]*"), "[local-path]"),
    (re.compile(r"\b(?:localhost|[\w-]+\.(?:local|internal|corp|lan))(?::\d+)?\b", re.I), "[private-host]"),
    (re.compile(r"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})(?::\d+)?\b"), "[private-host]"),
)


def _sanitize_query(raw: Any) -> tuple[str, bool]:
    query = str(raw or "").strip()
    original = query

    def _sanitize_url(match: re.Match[str]) -> str:
        try:
            parsed = urlparse(match.group(0))
        except ValueError:
            return "[url]"
        host = (parsed.hostname or "").lower()
        if (
            host in {"localhost", "127.0.0.1", "::1"}
            or host.endswith((".local", ".internal", ".corp", ".lan"))
        ):
            return "[private-url]"
        return host or "[url]"

    query = re.sub(r"https?://[^\s<>]+", _sanitize_url, query, flags=re.I)
    for pattern, replacement in _SENSITIVE_PATTERNS:
        query = pattern.sub(replacement, query)
    query = re.sub(r"\s+", " ", query).strip()
    return query[:1000], query != original


def _normalize_sites(raw: Any) -> list[str]:
    values = raw if isinstance(raw, list) else ([raw] if raw else ["stackoverflow"])
    sites: list[str] = []
    for value in values:
        site = str(value).strip().lower()
        if not site or not _SITE_RE.fullmatch(site):
            continue
        if site not in sites:
            sites.append(site)
        if len(sites) >= _MAX_SITES:
            break
    return sites or ["stackoverflow"]


def _normalize_tags(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    tags: list[str] = []
    for value in raw:
        tag = re.sub(r"[^a-zA-Z0-9.+#_-]", "", str(value).strip())[:35]
        if tag and tag not in tags:
            tags.append(tag)
        if len(tags) >= 5:
            break
    return tags


def _cache_key(query: str, sites: list[str], tags: list[str], max_results: int) -> str:
    return "\x1f".join((query.casefold(), ",".join(sites), ",".join(tags), str(max_results)))


def _cache_get(key: str) -> tuple[str, dict[str, Any]] | None:
    now = time.monotonic()
    cached = _cache.get(key)
    if cached is None:
        return None
    expires_at, content, meta = cached
    if expires_at <= now:
        del _cache[key]
        return None
    _cache.move_to_end(key)
    result_meta = dict(meta)
    result_meta["cache_hit"] = True
    return content, result_meta


def _cache_put(key: str, content: str, meta: dict[str, Any]) -> None:
    _cache[key] = (time.monotonic() + _CACHE_TTL_SECONDS, content, dict(meta))
    _cache.move_to_end(key)
    while len(_cache) > _CACHE_MAX_ITEMS:
        _cache.popitem(last=False)


def _date(value: Any) -> str:
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return "unknown"


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 16)].rstrip() + "\n... (truncated)"


def _answer_link(question_link: str, answer_id: Any) -> str:
    base = str(question_link or "").rstrip("/")
    return f"{base}/{answer_id}#answer-{answer_id}" if base and answer_id else base


def _select_answers(question: dict[str, Any], answers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    question_id = question.get("question_id")
    candidates = [a for a in answers if a.get("question_id") == question_id]
    if not candidates:
        return []
    accepted_id = question.get("accepted_answer_id")
    accepted = next((a for a in candidates if a.get("answer_id") == accepted_id), None)
    highest = max(candidates, key=lambda item: int(item.get("score") or 0))
    selected = [accepted] if accepted is not None else [highest]
    if accepted is not None and highest.get("answer_id") != accepted.get("answer_id"):
        selected.append(highest)
    return [item for item in selected if item is not None]


def _api_key() -> str:
    return os.environ.get("STACK_EXCHANGE_KEY", "").strip()


async def _api_get(path: str, *, params: dict[str, Any], method_key: str) -> dict[str, Any]:
    now = time.monotonic()
    retry_at = _backoff_until.get(method_key, 0.0)
    if retry_at > now:
        raise _StackExchangeError(f"API backoff active; retry in {math.ceil(retry_at - now)}s")
    request_params = dict(params)
    if key := _api_key():
        request_params["key"] = key
    try:
        client = await get_shared_httpx_client()
        response = await client.get(
            f"{_API_ROOT}{path}",
            params=request_params,
            headers={"User-Agent": "MiniAgent/2.0 StackExchangeSearch"},
            timeout=20.0,
            follow_redirects=False,
        )
        response.raise_for_status()
        data = response.json()
    except asyncio.CancelledError:
        raise
    except Exception as error:
        raise _StackExchangeError(f"{type(error).__name__}: Stack Exchange API request failed") from error
    if not isinstance(data, dict):
        raise _StackExchangeError("Stack Exchange API returned an invalid response")
    if error_name := data.get("error_name"):
        raise _StackExchangeError(f"Stack Exchange API error: {error_name}")
    try:
        backoff = max(0, int(data.get("backoff") or 0))
    except (TypeError, ValueError):
        backoff = 0
    if backoff:
        _backoff_until[method_key] = time.monotonic() + backoff
    return data


async def _search_site(
    site: str, query: str, tags: list[str], max_results: int
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "site": site,
        "q": query,
        "pagesize": max_results,
        "order": "desc",
        "sort": "relevance",
        "filter": "withbody",
    }
    if tags:
        params["tagged"] = ";".join(tags)
    questions_data = await _api_get(
        "/search/advanced", params=params, method_key=f"search:{site}"
    )
    questions = [item for item in questions_data.get("items", []) if isinstance(item, dict)]
    ids = [str(item.get("question_id")) for item in questions if item.get("question_id")]
    answers: list[dict[str, Any]] = []
    answer_quota: int | None = None
    if ids:
        answer_data = await _api_get(
            f"/questions/{';'.join(ids)}/answers",
            params={
                "site": site,
                "pagesize": 100,
                "order": "desc",
                "sort": "votes",
                "filter": "withbody",
            },
            method_key=f"answers:{site}",
        )
        answers = [item for item in answer_data.get("items", []) if isinstance(item, dict)]
        if isinstance(answer_data.get("quota_remaining"), int):
            answer_quota = answer_data["quota_remaining"]
    quotas = [
        value
        for value in (questions_data.get("quota_remaining"), answer_quota)
        if isinstance(value, int)
    ]
    return {
        "site": site,
        "questions": questions,
        "answers": answers,
        "quota_remaining": min(quotas) if quotas else None,
    }


def _format_site(result: dict[str, Any]) -> tuple[str, int]:
    site = str(result["site"])
    questions: list[dict[str, Any]] = result["questions"]
    answers: list[dict[str, Any]] = result["answers"]
    lines = [f"## {site}"]
    if not questions:
        lines.append("No matching questions found.")
        return "\n".join(lines), 0
    for index, question in enumerate(questions, 1):
        title = html.unescape(str(question.get("title") or "(untitled)"))
        link = str(question.get("link") or "")
        owner = question.get("owner") if isinstance(question.get("owner"), dict) else {}
        tags = ", ".join(str(tag) for tag in question.get("tags", []) if tag)
        lines.extend(
            [
                f"### {index}. {title}",
                f"Question: score={int(question.get('score') or 0)}, "
                f"answers={int(question.get('answer_count') or 0)}, "
                f"asked={_date(question.get('creation_date'))}, "
                f"active={_date(question.get('last_activity_date'))}",
                f"Author: {html.unescape(str(owner.get('display_name') or 'unknown'))}",
                f"Tags: {tags or '(none)'}",
                f"Link: {link or '(missing)'}",
            ]
        )
        question_excerpt = _clip(_html_to_text(question.get("body")), _QUESTION_EXCERPT_CHARS)
        if question_excerpt:
            lines.extend(["Question excerpt:", question_excerpt])
        selected = _select_answers(question, answers)
        if not selected:
            lines.append("Answer excerpt: no answer returned by the API.")
        for answer in selected:
            is_accepted = bool(answer.get("is_accepted"))
            label = "Accepted answer" if is_accepted else "Highest-voted answer"
            answer_owner = answer.get("owner") if isinstance(answer.get("owner"), dict) else {}
            lines.extend(
                [
                    f"{label}: score={int(answer.get('score') or 0)}, "
                    f"author={html.unescape(str(answer_owner.get('display_name') or 'unknown'))}, "
                    f"updated={_date(answer.get('last_activity_date'))}",
                    f"Answer link: {_answer_link(link, answer.get('answer_id'))}",
                    f"Content license: {answer.get('content_license') or question.get('content_license') or 'not returned'}",
                    _clip(_html_to_text(answer.get("body")), _ANSWER_EXCERPT_CHARS),
                ]
            )
        lines.append("")
    return "\n".join(lines).strip(), len(questions)


_stack_exchange_search_schema = {
    "type": "function",
    "function": {
        "name": "stack_exchange_search",
        "description": (
            "Search Stack Overflow and related Stack Exchange sites for practical troubleshooting "
            "experience. Returns question and accepted/high-voted answer excerpts with dates, votes, "
            "authors, and source links. Use for errors, compatibility, performance, drivers, networks, "
            "and hardware faults; not for routine conceptual questions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "maxLength": 1000,
                    "description": "Public-safe error signature, component, version, and environment",
                },
                "sites": {
                    "type": "array",
                    "maxItems": 3,
                    "items": {"type": "string"},
                    "description": "Stack Exchange API site parameters; defaults to stackoverflow",
                },
                "tags": {
                    "type": "array",
                    "maxItems": 5,
                    "items": {"type": "string"},
                    "description": "Optional tags required on matching questions",
                },
                "maxResults": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 5,
                    "description": "Maximum questions per site (default 3)",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
}


async def _stack_exchange_search_handler(
    args: dict[str, Any], _ctx: ToolContext
) -> ToolResult:
    query, redacted = _sanitize_query(args.get("query"))
    if not query or query in {"[credential]", "[email]", "[local-path]", "[private-host]"}:
        return ToolResult(
            success=False,
            content=f"{ERROR_PREFIX} query is empty after privacy sanitization",
            meta={"query_redacted": redacted, "cache_hit": False},
        )
    sites = _normalize_sites(args.get("sites"))
    tags = _normalize_tags(args.get("tags"))
    try:
        max_results = min(
            _MAX_RESULTS_PER_SITE, max(1, int(args.get("maxResults", 3)))
        )
    except (TypeError, ValueError):
        max_results = 3
    key = _cache_key(query, sites, tags, max_results)
    cached = _cache_get(key)
    if cached is not None:
        content, meta = cached
        return ToolResult(success=True, content=content, meta=meta)

    gathered = await asyncio.gather(
        *(_search_site(site, query, tags, max_results) for site in sites),
        return_exceptions=True,
    )
    successful: list[dict[str, Any]] = []
    failed_sites: list[str] = []
    site_errors: dict[str, str] = {}
    for site, result in zip(sites, gathered, strict=True):
        if isinstance(result, BaseException):
            failed_sites.append(site)
            site_errors[site] = str(result)
        else:
            successful.append(result)
    base_meta: dict[str, Any] = {
        "query": query,
        "query_redacted": redacted,
        "sites": sites,
        "failed_sites": failed_sites,
        "cache_hit": False,
    }
    if not successful:
        base_meta["site_errors"] = site_errors
        return ToolResult(
            success=False,
            content=(
                f"{ERROR_PREFIX} Stack Exchange search failed for all requested sites. "
                "Use another configured web source or state that external verification is unavailable."
            ),
            meta=base_meta,
        )

    sections: list[str] = [
        f"Stack Exchange search: {query}",
        "Community posts are experience reports; verify versions and commands against local evidence and official documentation.",
    ]
    total_results = 0
    for result in successful:
        section, count = _format_site(result)
        sections.append(section)
        total_results += count
    if failed_sites:
        sections.append(f"Unavailable sites: {', '.join(failed_sites)}")
    content = "\n\n".join(sections).strip()
    if len(content) > _MAX_OUTPUT_CHARS:
        content = content[: _MAX_OUTPUT_CHARS - 38].rstrip() + "\n\n... (tool output truncated at 16000 chars)"
    quotas = [
        result.get("quota_remaining")
        for result in successful
        if isinstance(result.get("quota_remaining"), int)
    ]
    base_meta.update(
        {
            "result_count": total_results,
            "quota_remaining": min(quotas) if quotas else None,
        }
    )
    _cache_put(key, content, base_meta)
    return ToolResult(success=True, content=content, meta=base_meta)


stackexchange_tools: dict[str, ToolDefinition] = {
    "stack_exchange_search": ToolDefinition(
        schema=_stack_exchange_search_schema,
        handler=_stack_exchange_search_handler,
        permission="allowlist",
        help_text="Search Stack Overflow and Stack Exchange troubleshooting posts",
        toolbox="web",
    )
}
