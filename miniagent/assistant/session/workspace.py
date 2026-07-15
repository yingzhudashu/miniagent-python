"""Mini Agent Python — 工作空间管理

管理会话的独立文件系统工作空间，包括：

- 工作空间创建/销毁
- 文件同步（继承父会话文件）
- 路径解析（默认工作目录）

执行阶段 ``ToolContext.cwd`` 与会话 ``files/`` 对齐由 ``UnifiedEngine`` / ``init_subsystems`` 注入；
若绕过引擎直接调用 ``run_pipeline``，须自行保证上下文与会话目录一致（见根目录 ``README`` 说明）。
"""

from __future__ import annotations

import asyncio
import os
import shutil


class WorkspaceManager:
    """工作空间管理器

    管理每个会话的独立文件工作空间。

    Example:
        wm = WorkspaceManager(base_dir="./workspaces/sessions")
        path = wm.create_workspace("session-1")
        # → "./workspaces/sessions/session-1/files"
    """

    def __init__(self, base_dir: str = "workspaces/sessions") -> None:
        """创建工作空间管理器

        Args:
            base_dir: 工作空间基础目录
        """
        self._base_dir = base_dir
        os.makedirs(self._base_dir, exist_ok=True)

    def create_workspace(
        self,
        session_id: str,
        parent_path: str | None = None,
        files_dir: str = "files",
        skills_dir: str = "skills",
    ) -> dict[str, str]:
        """创建工作空间

        Args:
            session_id: 会话 ID
            parent_path: 父工作空间路径（可选，用于继承文件）
            files_dir: 文件子目录名
            skills_dir: 技能子目录名

        Returns:
            包含 workspace_path, files_path, skills_path 的字典
        """
        workspace_path = os.path.join(self._base_dir, session_id)
        fp = os.path.join(workspace_path, files_dir)
        sp = os.path.join(workspace_path, skills_dir)

        os.makedirs(fp, exist_ok=True)
        os.makedirs(sp, exist_ok=True)

        # 如果有父工作空间，复制文件
        if parent_path and os.path.exists(parent_path):
            self._copy_tree(parent_path, fp)

        return {
            "workspace_path": workspace_path,
            "files_path": fp,
            "skills_path": sp,
        }

    def destroy_workspace(self, session_id: str) -> bool:
        """销毁工作空间

        Args:
            session_id: 会话 ID

        Returns:
            成功返回 True
        """
        workspace_path = os.path.join(self._base_dir, session_id)
        if os.path.exists(workspace_path):
            shutil.rmtree(workspace_path, ignore_errors=True)
            return True
        return False

    def get_workspace_path(self, session_id: str) -> str:
        """获取工作空间路径

        Args:
            session_id: 会话 ID

        Returns:
            工作空间的完整路径
        """
        return os.path.join(self._base_dir, session_id)

    @staticmethod
    def _copy_tree(src: str, dst: str) -> None:
        """复制目录树

        Args:
            src: 源目录
            dst: 目标目录
        """
        for item in os.listdir(src):
            s = os.path.join(src, item)
            d = os.path.join(dst, item)
            if os.path.isdir(s):
                shutil.copytree(s, d, dirs_exist_ok=True)
            else:
                shutil.copy2(s, d)

    @staticmethod
    async def _copy_tree_async(src: str, dst: str) -> None:
        """异步复制目录树（性能优化：不阻塞事件循环）。

        大工作空间复制可能耗时数秒，使用 asyncio.to_thread 包装，
        避免 LLM 流式处理被阻塞。

        Args:
            src: 源目录
            dst: 目标目录
        """
        # 复用同步实现，仅用 to_thread 包装以避免阻塞事件循环
        await asyncio.to_thread(WorkspaceManager._copy_tree, src, dst)

    async def create_workspace_async(
        self,
        session_id: str,
        parent_path: str | None = None,
        files_dir: str = "files",
        skills_dir: str = "skills",
    ) -> dict[str, str]:
        """异步创建工作空间（性能优化：复制文件不阻塞）。

        Args:
            session_id: 会话 ID
            parent_path: 父工作空间路径（可选，用于继承文件）
            files_dir: 文件子目录名
            skills_dir: 技能子目录名

        Returns:
            包含 workspace_path, files_path, skills_path 的字典
        """
        workspace_path = os.path.join(self._base_dir, session_id)
        fp = os.path.join(workspace_path, files_dir)
        sp = os.path.join(workspace_path, skills_dir)

        # 目录创建使用同步（快速操作，不阻塞）
        os.makedirs(fp, exist_ok=True)
        os.makedirs(sp, exist_ok=True)

        # 如果有父工作空间，异步复制文件
        if parent_path and os.path.exists(parent_path):
            await self._copy_tree_async(parent_path, fp)

        return {
            "workspace_path": workspace_path,
            "files_path": fp,
            "skills_path": sp,
        }

    async def destroy_workspace_async(self, session_id: str) -> bool:
        """异步销毁工作空间（性能优化：大目录删除不阻塞）。

        Args:
            session_id: 会话 ID

        Returns:
            成功返回 True
        """
        workspace_path = os.path.join(self._base_dir, session_id)
        if os.path.exists(workspace_path):
            await asyncio.to_thread(
                shutil.rmtree, workspace_path, ignore_errors=True
            )
            return True
        return False


__all__ = ["WorkspaceManager"]
