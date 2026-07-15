"""Mini Agent Python — 知识库基础类

KnowledgeBase：管理单个知识库的加载、索引构建和检索。
复用 miniagent.assistant.memory.keyword_index 的关键词提取逻辑。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from miniagent.agent.constants import KNOWLEDGE_MAX_FILE_CHARS
from miniagent.agent.logging import get_logger
from miniagent.assistant.knowledge.file_ingest import load_auto_file_metadata
from miniagent.assistant.memory.keyword_index import extract_keywords

_logger = get_logger(__name__)


def _max_file_chars() -> int:
    from miniagent.assistant.infrastructure.json_config import get_config

    return int(get_config("knowledge.max_file_chars", KNOWLEDGE_MAX_FILE_CHARS))


@dataclass
class KnowledgeEntry:
    """知识库条目：单个文件或片段的索引单元。

    Attributes:
        file_path: 文件路径（相对于知识库 files/）
        content: 文本内容（截断后）
        keywords: 提取的关键词列表
        metadata: 元数据（来源、时间等）
    """

    file_path: str
    content: str
    keywords: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class KBConfig:
    """知识库配置（从 KB.yaml 解析）。

    Attributes:
        name: 知识库名称（用于显示和引用）
        description: 知识库描述
        retriever: 检索策略（``keyword`` 关键词倒排；``fulltext`` 子串匹配）
        max_chars: 单次检索最大字符
        top_k: 返回条目数
        file_patterns: 包含的文件模式
    """

    name: str = "default"
    description: str = ""
    retriever: str = "keyword"
    max_chars: int = 8000
    top_k: int = 5
    file_patterns: list[str] = field(default_factory=lambda: ["*.md", "*.txt", "*.json"])


def load_kb_config(config_path: str) -> KBConfig:
    """加载 KB.yaml 配置文件。

    Args:
        config_path: KB.yaml 文件路径

    Returns:
        KBConfig 实例（文件不存在时返回默认配置）
    """
    if not os.path.isfile(config_path):
        return KBConfig(name=os.path.basename(os.path.dirname(config_path)))

    try:
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        _logger.warning("加载 KB.yaml 失败: %s - %s", config_path, e)
        return KBConfig(name=os.path.basename(os.path.dirname(config_path)))

    return KBConfig(
        name=data.get("name", os.path.basename(os.path.dirname(config_path))),
        description=data.get("description", ""),
        retriever=data.get("retriever", "keyword"),
        max_chars=int(data.get("max_chars", 8000)),
        top_k=int(data.get("top_k", 5)),
        file_patterns=data.get("file_patterns", ["*.md", "*.txt", "*.json"]),
    )


class KnowledgeBase:
    """知识库：管理文件集合的索引和检索。

    核心功能：
    - 加载文件目录或单个文件
    - 构建关键词倒排索引
    - 检索相关内容

    Example:
        kb = KnowledgeBase("/path/to/docs")
        kb.load()
        results = kb.search("API 文档")
    """

    def __init__(self, path: str, config: KBConfig | None = None) -> None:
        """创建知识库实例。

        Args:
            path: 知识库路径（目录或文件）
            config: 配置（None 时自动加载 KB.yaml）
        """
        self._path = os.path.abspath(path)
        self._config = config or load_kb_config(os.path.join(self._path, "KB.yaml"))

        # 索引数据
        self._entries: list[KnowledgeEntry] = []
        self._index: dict[str, list[int]] = {}  # keyword -> [entry indices]
        self._source_metadata: dict[str, dict[str, Any]] = {}
        self._loaded = False
        self._load_time: float = 0

    @property
    def name(self) -> str:
        """知识库名称"""
        return self._config.name

    @property
    def description(self) -> str:
        """知识库描述"""
        return self._config.description

    @property
    def path(self) -> str:
        """知识库路径"""
        return self._path

    @property
    def stats(self) -> dict[str, Any]:
        """知识库统计信息"""
        return {
            "name": self.name,
            "path": self._path,
            "entries": len(self._entries),
            "keywords": len(self._index),
            "loaded": self._loaded,
            "load_time": self._load_time,
        }

    def load(self) -> None:
        """加载知识库：扫描文件、构建索引。"""
        if self._loaded:
            return

        start_time = time.time()

        # 确定文件列表
        files: list[str] = []
        if os.path.isfile(self._path):
            files = [self._path]
        elif os.path.isdir(self._path):
            self._source_metadata = load_auto_file_metadata(self._path)
            files_dir = os.path.join(self._path, "files")
            if os.path.isdir(files_dir):
                # 优先使用 files/ 子目录
                files = self._scan_files(files_dir, self._config.file_patterns)
            else:
                # 否则扫描整个目录
                files = self._scan_files(self._path, self._config.file_patterns)

        # 加载文件内容
        for fp in files:
            try:
                entry = self._load_file(fp)
                if entry:
                    self._entries.append(entry)
            except Exception as e:
                _logger.warning("加载知识库文件失败: %s - %s", fp, e)

        # 构建索引
        self._build_index()

        self._loaded = True
        self._load_time = time.time() - start_time
        _logger.info(
            "知识库已加载: %s (%d 条目, %d 关键词, %.2fs)",
            self.name, len(self._entries), len(self._index), self._load_time,
        )

    def _scan_files(self, directory: str, patterns: list[str]) -> list[str]:
        """扫描目录中匹配模式的文件（去重）。"""
        seen: dict[str, None] = {}
        for pattern in patterns:
            for fp in Path(directory).glob(pattern):
                if fp.is_file():
                    seen[str(fp.resolve())] = None
        return sorted(seen.keys())

    def _load_file(self, file_path: str) -> KnowledgeEntry | None:
        """加载单个文件为 KnowledgeEntry。"""
        try:
            with open(file_path, encoding="utf-8") as f:
                content = f.read()
        except UnicodeDecodeError:
            # 尝试其他编码
            try:
                with open(file_path, encoding="gbk") as f:
                    content = f.read()
            except Exception:
                return None

        # 截断大文件
        max_chars = _max_file_chars()
        if len(content) > max_chars:
            content = content[:max_chars] + "\n...[已截断]"

        # 计算相对路径
        if os.path.isdir(self._path):
            files_dir = os.path.join(self._path, "files")
            if os.path.isdir(files_dir) and file_path.startswith(files_dir):
                rel_path = os.path.relpath(file_path, files_dir)
            else:
                rel_path = os.path.relpath(file_path, self._path)
        else:
            rel_path = os.path.basename(file_path)

        # 提取关键词
        keywords = extract_keywords(content)

        metadata = {"source": file_path, "size": len(content)}
        source_meta = self._source_metadata_for_rel_path(rel_path)
        if source_meta:
            metadata.update(source_meta)

        return KnowledgeEntry(
            file_path=rel_path,
            content=content,
            keywords=keywords,
            metadata=metadata,
        )

    def _build_index(self) -> None:
        """构建关键词倒排索引。"""
        self._index.clear()
        for i, entry in enumerate(self._entries):
            for kw in entry.keywords:
                self._index.setdefault(kw, []).append(i)

    def rank_entries(self, query: str) -> list[tuple[int, float]]:
        """按相关性对条目打分并排序。

        Args:
            query: 搜索关键词或问题

        Returns:
            ``(entry_index, score)`` 列表，按分数降序
        """
        self.load()
        if not self._entries or not query.strip():
            return []

        retriever = (self._config.retriever or "keyword").lower()
        if retriever == "fulltext":
            return self._rank_fulltext(query)
        return self._rank_keyword(query)

    def format_ranked_entry(self, index: int, *, kb_label: str | None = None) -> str:
        """格式化单条检索结果。"""
        entry = self._entries[index]
        snippet = entry.content[:500]
        if len(entry.content) > 500:
            snippet += "..."

        title = entry.file_path
        if kb_label:
            title = f"[{kb_label}] {title}"

        source_line = self._format_source_metadata(entry)
        if source_line:
            return f"### {title}\n{source_line}\n{snippet}\n"
        return f"### {title}\n{snippet}\n"

    def search(self, query: str, top_k: int | None = None, max_chars: int | None = None) -> str:
        """检索知识库内容。

        Args:
            query: 搜索关键词
            top_k: 返回条目数（None 时使用配置默认值）
            max_chars: 最大字符数（None 时使用配置默认值）

        Returns:
            格式化的检索结果文本
        """
        self.load()

        if not self._entries:
            return ""

        top_k = top_k or self._config.top_k
        max_chars = max_chars or self._config.max_chars
        scores = self.rank_entries(query)
        return self._build_search_output(scores, top_k, max_chars)

    def _rank_keyword(self, query: str) -> list[tuple[int, float]]:
        query_keywords = extract_keywords(query)
        scores: list[tuple[int, float]] = []
        for i, entry in enumerate(self._entries):
            score = 0.0
            for kw in query_keywords:
                if kw in entry.keywords:
                    weight = 1.5 if len(kw) == 3 else 1.0
                    score += weight
            if score > 0:
                scores.append((i, score))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores

    def _rank_fulltext(self, query: str) -> list[tuple[int, float]]:
        query_lower = query.lower().strip()
        terms = extract_keywords(query)
        if not terms and query_lower:
            terms = [query_lower]

        scores: list[tuple[int, float]] = []
        for i, entry in enumerate(self._entries):
            haystack = entry.content.lower()
            score = 0.0
            if query_lower and query_lower in haystack:
                score += 2.0
            for term in terms:
                term_lower = term.lower()
                if term_lower in haystack:
                    weight = 1.5 if len(term_lower) == 3 else 1.0
                    score += weight
            if score > 0:
                scores.append((i, score))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores

    def _build_search_output(
        self,
        scores: list[tuple[int, float]],
        top_k: int,
        max_chars: int,
    ) -> str:
        results: list[str] = []
        total_chars = 0

        for i, _score in scores[:top_k]:
            text = self.format_ranked_entry(i)
            if total_chars + len(text) > max_chars:
                break
            results.append(text)
            total_chars += len(text)

        if not results:
            return ""

        header = f"## 知识库: {self.name}\n\n"
        return header + "\n".join(results)

    def reload(self) -> None:
        """重新加载知识库。"""
        self._entries.clear()
        self._index.clear()
        self._source_metadata = {}
        self._loaded = False
        self.load()

    def _source_metadata_for_rel_path(self, rel_path: str) -> dict[str, Any]:
        for meta in self._source_metadata.values():
            if meta.get("file_path") == rel_path:
                return dict(meta)
        return {}

    def _format_source_metadata(self, entry: KnowledgeEntry) -> str:
        source_path = entry.metadata.get("source_path") or entry.metadata.get("source")
        if not source_path:
            return ""
        parts = [f"来源: `{source_path}`"]
        if entry.metadata.get("source_hash"):
            parts.append(f"hash: `{str(entry.metadata['source_hash'])[:12]}`")
        if entry.metadata.get("size") is not None:
            parts.append(f"size: {entry.metadata['size']}")
        if entry.metadata.get("ingested_at"):
            parts.append(f"ingested_at: {entry.metadata['ingested_at']}")
        return " | ".join(parts)


def resolve_kb_file_path(kb_root: str, file_path: str) -> str | None:
    """解析知识库内文件路径，拒绝目录穿越。

    依次尝试 ``files/<file_path>`` 与 ``<file_path>``（均相对于知识库根目录）。

    Args:
        kb_root: 知识库根目录
        file_path: 相对路径

    Returns:
        绝对路径；无法安全解析或文件不存在时返回 ``None``
    """
    kb_root = os.path.abspath(kb_root)
    normalized = file_path.replace("\\", "/").strip().lstrip("/")
    if not normalized or ".." in Path(normalized).parts:
        return None

    for rel in (os.path.join("files", normalized), normalized):
        candidate = os.path.abspath(os.path.join(kb_root, rel))
        try:
            if os.path.commonpath([kb_root, candidate]) != kb_root:
                continue
        except ValueError:
            continue
        if os.path.isfile(candidate):
            return candidate
    return None


__all__ = [
    "KnowledgeBase",
    "KnowledgeEntry",
    "KBConfig",
    "load_kb_config",
    "resolve_kb_file_path",
]
