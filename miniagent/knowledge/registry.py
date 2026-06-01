"""Mini Agent Python — 知识库注册表

管理多个知识库的挂载、卸载和检索。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from miniagent.infrastructure.logger import get_logger
from miniagent.knowledge.base import KnowledgeBase

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
            state_dir: 状态存储目录（默认 MINI_AGENT_STATE/knowledge）
        """
        if state_dir is None:
            state_dir = os.environ.get(
                "MINI_AGENT_STATE",
                os.path.join(os.getcwd(), "workspaces"),
            )
        self._state_dir = state_dir
        self._kb_dir = os.environ.get("MINIAGENT_KB_ROOT", _DEFAULT_KB_ROOT)

        # 已挂载的知识库：name -> KnowledgeBase
        self._mounted: dict[str, KnowledgeBase] = {}

        # 加载已保存的挂载状态
        self._load_registry()

        # 自动挂载默认知识库
        if os.environ.get("MINIAGENT_KB_AUTO_MOUNT", "1") not in ("0", "false", "no"):
            self._auto_mount()

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
                if path and os.path.exists(path):
                    kb = KnowledgeBase(path)
                    kb.load()
                    self._mounted[kb.name] = kb
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
            except Exception:
                pass

        data = {
            "mounted": [
                {"name": kb.name, "path": kb.path, "mounted_at": time.time()}
                for kb in self._mounted.values()
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

        # 扫描知识库目录
        for name in os.listdir(kb_root):
            kb_path = os.path.join(kb_root, name)
            if os.path.isdir(kb_path) and name not in self._mounted:
                # 检查是否有 KB.yaml 或 files 目录
                config_path = os.path.join(kb_path, "KB.yaml")
                files_dir = os.path.join(kb_path, "files")
                if os.path.isfile(config_path) or os.path.isdir(files_dir):
                    try:
                        kb = KnowledgeBase(kb_path)
                        kb.load()
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
                return f"⚠️ 知识库 '{kb_name}' 未挂载"
            return self._mounted[kb_name].search(query, top_k, max_chars)

        # 跨知识库检索
        results: list[str] = []
        total_chars = 0
        max_chars = max_chars or int(os.environ.get("MINIAGENT_KB_MAX_CHARS", "8000"))

        for kb in self._mounted.values():
            result = kb.search(query, top_k)
            if result:
                if total_chars + len(result) > max_chars:
                    break
                results.append(result)
                total_chars += len(result)

        if not results:
            return ""

        return "\n---\n".join(results)

    def get_kb(self, name: str) -> KnowledgeBase | None:
        """获取指定知识库实例。"""
        return self._mounted.get(name)

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


# ─── 全局实例（进程级单例）────────────────────────────────────

_GLOBAL_REGISTRY: KnowledgeRegistry | None = None


def get_kb_registry(state_dir: str | None = None) -> KnowledgeRegistry:
    """获取知识库注册表实例（进程级单例）。"""
    global _GLOBAL_REGISTRY
    if _GLOBAL_REGISTRY is None:
        _GLOBAL_REGISTRY = KnowledgeRegistry(state_dir)
    return _GLOBAL_REGISTRY


def mount_knowledge_base(path: str, name: str | None = None) -> dict[str, Any]:
    """挂载知识库（便捷函数）。"""
    return get_kb_registry().mount(path, name)


def unmount_knowledge_base(name: str) -> dict[str, Any]:
    """卸载知识库（便捷函数）。"""
    return get_kb_registry().unmount(name)


def search_knowledge(
    query: str,
    kb_name: str | None = None,
    top_k: int | None = None,
    max_chars: int | None = None,
) -> str:
    """检索知识库（便捷函数）。"""
    return get_kb_registry().search(query, kb_name, top_k, max_chars)


__all__ = [
    "KnowledgeRegistry",
    "get_kb_registry",
    "mount_knowledge_base",
    "unmount_knowledge_base",
    "search_knowledge",
]