"""Session ID 安全化处理

统一各模块中 session_id 的安全化逻辑，确保同一 session_key 在不同模块产生一致的结果。

使用规则：
- 将所有非字母、数字、下划线、连字符的字符替换为下划线
- 确保生成的字符串可作为安全的文件名/目录名

使用方式：
    from miniagent.utils.session_id import safe_session_id

    safe = safe_session_id("feishu:oc_abc123")
    # 结果: "feishu_oc_abc123"

相关模块：
- miniagent/engine/session_lock.py
- miniagent/session/manager.py
- miniagent/memory/history_archive.py
- miniagent/memory/layered_memory.py
- miniagent/memory/store.py
"""

from __future__ import annotations

import re

# 统一使用更严格的正则：仅保留字母、数字、下划线和连字符
# 这确保生成的 ID 在所有文件系统上都是安全的
_SAFE_SESSION_ID_PATTERN = re.compile(r"[^a-zA-Z0-9_-]")


def safe_session_id(session_key: str | None) -> str:
    """将 session_key 转换为安全的文件名/目录名组件。

    替换所有非安全字符为下划线，确保：
    - 可作为文件名/目录名使用（无路径分隔符、特殊字符）
    - 在 Windows/Linux/macOS 上均可使用
    - 不同模块对同一 session_key 产生一致结果

    边界行为：
    - ``None`` 或空字符串返回 ``""``
    - 点号 ``.`` 等非字母数字字符同样替换为 ``_``（如 ``user@example.com`` → ``user_example_com``）
    - 连续非法字符各自替换，不会合并（如 ``".."`` → ``"__"``）
    - 不做长度截断、哈希或 Windows 保留名（``CON`` 等）特殊处理

    Args:
        session_key: 原始会话标识符（如 ``"feishu:oc_abc123"``）；``None`` 视为空。

    Returns:
        安全化的会话标识符（如 ``"feishu_oc_abc123"``）

    Examples:
        >>> safe_session_id("cli-session-1")
        'cli-session-1'
        >>> safe_session_id("feishu:oc_abc123")
        'feishu_oc_abc123'
        >>> safe_session_id("test/session")
        'test_session'
        >>> safe_session_id(None)
        ''
        >>> safe_session_id("user@example.com")
        'user_example_com'
    """
    return _SAFE_SESSION_ID_PATTERN.sub("_", session_key or "")


__all__ = ["safe_session_id"]
