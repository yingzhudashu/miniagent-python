"""执行阶段的流式文本聚合组件。"""

from __future__ import annotations


class StreamingBuffer:
    """以分块列表聚合流式文本，避免逐字符拼接造成二次复杂度。

    缓冲区只由单个执行协程拥有，不提供线程安全保证。超过 50 个分块时会合并
    已有内容，控制列表和临时对象数量；``len`` 始终返回文本字符总数。
    """

    __slots__ = ("_chunks", "_length", "_consolidated")

    def __init__(self) -> None:
        """初始化空缓冲区。"""
        self._chunks: list[str] = []
        self._length = 0
        self._consolidated: str | None = None

    def append(self, chunk: str) -> None:
        """追加一个文本分块；空分块也保持与输入流一致的语义。"""
        self._chunks.append(chunk)
        self._length += len(chunk)
        if len(self._chunks) > 50:
            self._consolidated = "".join(self._chunks)
            self._chunks = [self._consolidated]

    def getvalue(self) -> str:
        """返回目前聚合的完整文本，不改变缓冲状态。"""
        if self._consolidated is None:
            return "".join(self._chunks)
        if len(self._chunks) == 1:
            return self._consolidated
        return self._consolidated + "".join(self._chunks[1:])

    def __len__(self) -> int:
        """返回聚合文本的字符数。"""
        return self._length

    def clear(self) -> None:
        """清空所有文本与合并状态，以便安全复用。"""
        self._chunks.clear()
        self._length = 0
        self._consolidated = None


__all__ = ["StreamingBuffer"]
