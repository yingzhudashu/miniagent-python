"""Mini Agent Python — 知识库注册表

管理多个知识库的挂载、卸载和检索。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

from miniagent.agent.logging import get_logger
from miniagent.agent.types.error_prefix import WARNING_PREFIX
from miniagent.assistant.infrastructure.json_config import get_config
from miniagent.assistant.knowledge.base import KnowledgeBase
from miniagent.assistant.memory.keyword_index import extract_keywords
from miniagent.assistant.state import StateSchemaError
from miniagent.assistant.state.sync import immediate_transaction, open_state_database

_logger = get_logger(__name__)

# 默认知识库根目录
_DEFAULT_KB_ROOT = "workspaces/knowledge"


def _fts_query(query: str) -> str:
    terms = extract_keywords(query)
    if not terms and query.strip():
        terms = [query.strip()]
    return " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)


class KnowledgeRegistry:
    """知识库注册表：管理多个知识库的挂载、卸载和检索。

    核心功能：
    - 挂载知识库（目录或文件）
    - 卸载知识库
    - 跨知识库检索
    - 持久化挂载状态

    Example:
        registry = KnowledgeRegistry()
        registry.mount("/path/to/docs")
        results = registry.search("API 文档")
    """

    def __init__(self, state_dir: str | None = None) -> None:
        """创建知识库注册表。

        Args:
            state_dir: 保留供测试与扩展；知识库根目录由 ``knowledge.root`` 配置决定。
        """
        if state_dir is None:
            from miniagent.assistant.infrastructure.paths import resolve_state_dir

            state_dir = resolve_state_dir()
        self._state_dir = state_dir
        self._kb_dir = get_config(
            "knowledge.root",
            get_config("knowledge.default_root", _DEFAULT_KB_ROOT),
        )

        # 已挂载的知识库：mount_name -> KnowledgeBase
        self._mounted: dict[str, KnowledgeBase] = {}

        self._load_registry()

        if get_config("knowledge.auto_mount", True):
            self._auto_mount()

    def _is_path_mounted(self, path: str) -> bool:
        abs_path = os.path.abspath(path)
        return any(os.path.abspath(kb.path) == abs_path for kb in self._mounted.values())

    def _load_registry(self) -> None:
        """Load current mount records from the project database."""
        try:
            with open_state_database(self._state_dir) as connection:
                rows = connection.execute(
                    "SELECT name, source_path FROM knowledge_mounts ORDER BY name"
                ).fetchall()
            for item in rows:
                path = str(item[1])
                if not path or not os.path.exists(path):
                    continue
                abs_path = os.path.abspath(path)
                if self._is_path_mounted(abs_path):
                    continue
                kb = KnowledgeBase(abs_path)
                kb.load()
                mount_name = str(item[0]) or kb.name
                self._mounted[mount_name] = kb
        except StateSchemaError:
            raise
        except Exception as e:
            _logger.warning("加载知识库注册表失败: %s", e)

    def _save_registry(self) -> None:
        """Atomically persist the current mount set."""
        try:
            now_ms = int(time.time() * 1000)
            with open_state_database(self._state_dir) as connection:
                with immediate_transaction(connection):
                    names = set(self._mounted)
                    if names:
                        placeholders = ",".join("?" for _ in names)
                        # Only generated ``?`` placeholders enter the SQL structure.
                        stale_query = (
                            "SELECT d.id FROM knowledge_documents d "  # nosec B608
                            "JOIN knowledge_mounts m ON m.id=d.mount_id "
                            f"WHERE m.name NOT IN ({placeholders})"
                        )
                        stale_ids = connection.execute(
                            stale_query,
                            tuple(sorted(names)),
                        ).fetchall()
                        connection.executemany(
                            "DELETE FROM knowledge_fts WHERE rowid=?",
                            ((int(row[0]),) for row in stale_ids),
                        )
                        delete_mounts_query = (
                            f"DELETE FROM knowledge_mounts WHERE name NOT IN ({placeholders})"  # nosec B608
                        )
                        connection.execute(
                            delete_mounts_query,
                            tuple(sorted(names)),
                        )
                    else:
                        connection.execute("DELETE FROM knowledge_fts")
                        connection.execute("DELETE FROM knowledge_mounts")
                    for mount_name, kb in self._mounted.items():
                        connection.execute(
                            """INSERT INTO knowledge_mounts(name, source_path, updated_at_ms)
                               VALUES (?, ?, ?)
                               ON CONFLICT(name) DO UPDATE SET
                                 source_path=excluded.source_path,
                                 updated_at_ms=excluded.updated_at_ms""",
                            (mount_name, kb.path, now_ms),
                        )
                        mount_row = connection.execute(
                            "SELECT id FROM knowledge_mounts WHERE name=?",
                            (mount_name,),
                        ).fetchone()
                        assert mount_row is not None
                        mount_id = int(mount_row[0])
                        old_documents = connection.execute(
                            "SELECT id FROM knowledge_documents WHERE mount_id=?",
                            (mount_id,),
                        ).fetchall()
                        connection.executemany(
                            "DELETE FROM knowledge_fts WHERE rowid=?",
                            ((int(row[0]),) for row in old_documents),
                        )
                        connection.execute(
                            "DELETE FROM knowledge_documents WHERE mount_id=?",
                            (mount_id,),
                        )
                        for entry in kb._entries:
                            content_hash = hashlib.blake2s(
                                entry.content.encode("utf-8")
                            ).hexdigest()
                            cursor = connection.execute(
                                """INSERT INTO knowledge_documents(
                                       mount_id, relative_path, title, content,
                                       content_hash, metadata_json, updated_at_ms
                                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                                (
                                    mount_id,
                                    entry.file_path,
                                    entry.file_path,
                                    entry.content,
                                    content_hash,
                                    json.dumps(
                                        entry.metadata,
                                        ensure_ascii=False,
                                        separators=(",", ":"),
                                        allow_nan=False,
                                    ),
                                    now_ms,
                                ),
                            )
                            document_id = int(cursor.lastrowid or 0)
                            connection.execute(
                                "INSERT INTO knowledge_fts(rowid, title, content) VALUES (?, ?, ?)",
                                (document_id, entry.file_path, entry.content),
                            )
        except StateSchemaError:
            raise
        except Exception as e:
            _logger.warning("保存知识库注册表失败: %s", e)

    def _auto_mount(self) -> None:
        """自动挂载知识库根目录下的默认知识库。"""
        kb_root = self._kb_dir
        if not os.path.isdir(kb_root):
            return

        for name in os.listdir(kb_root):
            kb_path = os.path.join(kb_root, name)
            if not os.path.isdir(kb_path):
                continue
            if self._is_path_mounted(kb_path):
                continue
            config_path = os.path.join(kb_path, "KB.yaml")
            files_dir = os.path.join(kb_path, "files")
            if os.path.isfile(config_path) or os.path.isdir(files_dir):
                try:
                    kb = KnowledgeBase(kb_path)
                    kb.load()
                    if kb.name in self._mounted:
                        _logger.debug("跳过自动挂载（名称已占用）: %s", kb.name)
                        continue
                    self._mounted[kb.name] = kb
                    _logger.info("自动挂载知识库: %s", kb.name)
                except Exception as e:
                    _logger.warning("自动挂载失败: %s - %s", name, e)
        if self._mounted:
            self._save_registry()

    def mount(self, path: str, name: str | None = None) -> dict[str, Any]:
        """挂载知识库。

        Args:
            path: 知识库路径（目录或文件）
            name: 知识库名称（None 时自动推断）

        Returns:
            操作结果（success, message, kb_name）
        """
        path = os.path.abspath(path)
        if not os.path.exists(path):
            return {"success": False, "message": f"路径不存在: {path}"}

        try:
            kb = KnowledgeBase(path)
            kb.load()

            # 名称冲突检测
            kb_name = name or kb.name
            if kb_name in self._mounted and self._mounted[kb_name].path != path:
                return {
                    "success": False,
                    "message": f"知识库 '{kb_name}' 已存在，请先卸载",
                }

            self._mounted[kb_name] = kb
            self._save_registry()

            return {
                "success": True,
                "message": f"已挂载知识库: {kb_name} ({len(kb._entries)} 条目)",
                "kb_name": kb_name,
                "stats": kb.stats,
            }
        except Exception as e:
            return {"success": False, "message": f"挂载失败: {e}"}

    def unmount(self, name: str) -> dict[str, Any]:
        """卸载知识库。

        Args:
            name: 知识库名称

        Returns:
            操作结果
        """
        if name not in self._mounted:
            return {"success": False, "message": f"知识库 '{name}' 未挂载"}

        del self._mounted[name]
        self._save_registry()

        return {"success": True, "message": f"已卸载知识库: {name}"}

    def list(self) -> list[dict[str, Any]]:
        """列出已挂载的知识库。

        Returns:
            知识库统计信息列表
        """
        return [kb.stats for kb in self._mounted.values()]

    def search(
        self,
        query: str,
        kb_name: str | None = None,
        top_k: int | None = None,
        max_chars: int | None = None,
    ) -> str:
        """检索知识库内容。

        Args:
            query: 搜索关键词
            kb_name: 知识库名称（None 时检索所有）
            top_k: 返回条目数
            max_chars: 最大字符数

        Returns:
            格式化的检索结果文本
        """
        if kb_name:
            # 单知识库检索
            if kb_name not in self._mounted:
                return f"{WARNING_PREFIX} 知识库 '{kb_name}' 未挂载"
        # The durable FTS index is the single search implementation for mounted KBs.
        effective_top_k = top_k
        if effective_top_k is None:
            effective_top_k = int(get_config("knowledge.top_k", 5))
        expression = _fts_query(query)
        if not expression:
            return ""
        parameters: list[Any] = [expression]
        where = "knowledge_fts MATCH ?"
        if kb_name:
            where += " AND m.name=?"
            parameters.append(kb_name)
        parameters.append(max(0, effective_top_k))
        # ``where`` contains only the two fixed clauses assembled above.
        search_query = (
            "SELECT m.name, d.title, d.content, d.metadata_json, "  # nosec B608
            "bm25(knowledge_fts) AS rank "
            "FROM knowledge_fts f "
            "JOIN knowledge_documents d ON d.id=f.rowid "
            "JOIN knowledge_mounts m ON m.id=d.mount_id "
            f"WHERE {where} "
            "ORDER BY rank, d.id LIMIT ?"
        )
        with open_state_database(self._state_dir) as connection:
            rows = connection.execute(
                search_query,
                tuple(parameters),
            ).fetchall()

        results: list[str] = []
        total_chars = 0
        max_chars = max_chars or get_config("knowledge.max_chars", 8000)
        multi_kb = len(self._mounted) > 1 and kb_name is None

        for row in rows:
            metadata = json.loads(str(row[3]))
            title = str(row[1])
            if multi_kb:
                title = f"[{row[0]}] {title}"
            content = str(row[2])
            snippet = content[:500] + ("..." if len(content) > 500 else "")
            source = metadata.get("source_path") or metadata.get("source")
            source_line = f"来源: `{source}`\n" if source else ""
            text = f"### {title}\n{source_line}{snippet}\n"
            if total_chars + len(text) > max_chars:
                break
            results.append(text)
            total_chars += len(text)

        if not results:
            return ""

        body = "\n---\n".join(results)
        if kb_name:
            return f"## 知识库: {self._mounted[kb_name].name}\n\n{body}"
        return body

    def get_kb(self, name: str) -> KnowledgeBase | None:
        """获取指定知识库实例。"""
        return self._mounted.get(name)

    def refresh_auto_file_kb(self, path: str, name: str) -> dict[str, Any]:
        """挂载或重载项目级自动入库知识库。

        Args:
            path: 知识库目录绝对路径
            name: 注册表中的稳定挂载名称（如 ``_auto_file_analysis``）

        Returns:
            操作结果（success, message, kb_name）
        """
        path = os.path.abspath(path)
        kb = self._mounted.get(name)
        if kb and os.path.abspath(kb.path) == path:
            kb.reload()
            self._save_registry()
            return {"success": True, "message": f"已刷新知识库: {name}", "kb_name": name}

        if kb and os.path.abspath(kb.path) != path:
            del self._mounted[name]

        return self.mount(path, name)

    def reload(self, name: str | None = None) -> dict[str, Any]:
        """重新加载知识库。

        Args:
            name: 知识库名称（None 时重载所有）

        Returns:
            操作结果
        """
        if name:
            if name not in self._mounted:
                return {"success": False, "message": f"知识库 '{name}' 未挂载"}
            self._mounted[name].reload()
            self._save_registry()
            return {"success": True, "message": f"已重载知识库: {name}"}

        # 重载所有
        for kb in self._mounted.values():
            kb.reload()
        self._save_registry()
        return {"success": True, "message": f"已重载 {len(self._mounted)} 个知识库"}


__all__ = ["KnowledgeRegistry"]
