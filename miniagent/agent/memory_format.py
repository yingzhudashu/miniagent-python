"""Deterministic prompt formatting for injected session-memory DTOs."""

from __future__ import annotations

from miniagent.agent.ground_truth import format_ground_truth_for_prompt
from miniagent.agent.types.memory import SessionMemory


def format_memory_for_prompt(memory: SessionMemory | None) -> str:
    """将结构化会话记忆格式化为有界的提示词片段。"""
    if not memory:
        return ""
    parts: list[str] = []
    ground_truth = format_ground_truth_for_prompt(memory)
    if ground_truth:
        parts.append("## 确定事实")
        parts.extend(f"- {fact}" for fact in ground_truth)
    if memory.key_facts:
        parts.append("## 关键记忆")
        seen = {line.lower().strip() for line in ground_truth}
        parts.extend(
            f"- {fact}"
            for fact in memory.key_facts[-10:]
            if not any(fact.lower().strip() in line for line in seen)
        )
    if memory.uploaded_files:
        parts.append("## 上传的文件")
        for item in memory.uploaded_files[-10:]:
            label = {"image": "图片", "text": "文本", "binary": "文件"}.get(item.type, "文件")
            size = f"{item.size // 1024}KB" if item.size >= 1024 else f"{item.size}B"
            parts.append(f"- {item.name} ({label}, {size})")
            if item.description:
                suffix = "…" if len(item.description) > 200 else ""
                parts.append(f"  内容: {item.description[:200]}{suffix}")
    if memory.cumulative_summary:
        parts.extend(("## 之前的对话摘要", memory.cumulative_summary))
    if memory.entries:
        parts.append("## 最近的对话")
        for entry in memory.entries[-5:]:
            timestamp = entry.timestamp[:16].replace("T", " ")
            parts.append(f"[{timestamp}] 用户: {entry.user_snippet} → 摘要: {entry.summary}")
    if not parts:
        return ""
    return "【历史记忆】\n\n" + "\n\n".join(parts) + "\n\n【记忆结束】"


__all__ = ["format_memory_for_prompt"]
