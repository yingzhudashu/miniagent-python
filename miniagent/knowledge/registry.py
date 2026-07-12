"""Mini Agent Python — 知识库注册表

管理多个知识库的挂载、卸载和检索。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from miniagent.infrastructure.json_config import get_config
from miniagent.infrastructure.logger import get_logger
from miniagent.knowledge.base import KnowledgeBase
from miniagent.types.error_prefix import WARNING_PREFIX

_logger = get_logger(__name__)

# 默认知识库根目录
_DEFAULT_KB_ROOT = "workspaces/knowledge"

# 注册表文件名
_REGISTRY_FILE = "kb_registry.json"


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
            from miniagent.infrastructure.paths import resolve_state_dir

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
        """从磁盘加载挂载状态。"""
        registry_path = os.path.join(self._kb_dir, _REGISTRY_FILE)
        if not os.path.isfile(registry_path):
            return

        try:
            with open(registry_path, encoding="utf-8") as f:
                data = json.load(f)
            for item in data.get("mounted", []):
                path = item.get("path", "")
                if not path or not os.path.exists(path):
                    continue
                abs_path = os.path.abspath(path)
                if self._is_path_mounted(abs_path):
                    continue
                kb = KnowledgeBase(abs_path)
                kb.load()
                mount_name = item.get("name") or kb.name
                self._mounted[mount_name] = kb
        except Exception as e:
            _logger.warning("加载知识库注册表失败: %s", e)

    def _save_registry(self) -> None:
        """保存挂载状态到磁盘。"""
        registry_path = os.path.join(self._kb_dir, _REGISTRY_FILE)

        # 确保目录存在
        kb_dir = os.path.dirname(registry_path)
        if kb_dir and not os.path.isdir(kb_dir):
            try:
                os.makedirs(kb_dir, exist_ok=True)
            except Exception as e:
                _logger.debug("创建知识库目录失败: %s", e)

        data = {
            "mounted": [
                {"name": mount_name, "path": kb.path, "mounted_at": time.time()}
                for mount_name, kb in self._mounted.items()
            ],
            "updated_at": time.time(),
        }

        try:
            with open(registry_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
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
            return self._mounted[kb_name].search(query, top_k, max_chars)

        # 跨知识库检索：合并打分后取全局 top_k
        merged: list[tuple[KnowledgeBase, int, float]] = []
        effective_top_k = top_k
        if effective_top_k is None:
            effective_top_k = int(get_config("knowledge.top_k", 5))

        for kb in self._mounted.values():
            for idx, score in kb.rank_entries(query):
                merged.append((kb, idx, score))

        merged.sort(key=lambda x: x[2], reverse=True)
        merged = merged[:effective_top_k]

        results: list[str] = []
        total_chars = 0
        max_chars = max_chars or get_config("knowledge.max_chars", 8000)
        multi_kb = len(self._mounted) > 1

        for kb, idx, _score in merged:
            label = kb.name if multi_kb else None
            text = kb.format_ranked_entry(idx, kb_label=label)
            if total_chars + len(text) > max_chars:
                break
            results.append(text)
            total_chars += len(text)

        if not results:
            return ""

        return "\n---\n".join(results)

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
            return {"success": True, "message": f"已重载知识库: {name}"}

        # 重载所有
        for kb in self._mounted.values():
            kb.reload()
        return {"success": True, "message": f"已重载 {len(self._mounted)} 个知识库"}


__all__ = ["KnowledgeRegistry"]
