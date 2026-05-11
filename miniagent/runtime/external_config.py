"""可选外部 JSON（遗留兼容），由 ``MINIAGENT_CONFIG`` 或 ``MINIAGENT_OPENCLAW_CONFIG`` 指定路径。

推荐在 ``.env`` 中直接配置 ``OPENAI_*``、``AGENT_*``、``AGENT_THINKING_DEFAULT``、
``OPENAI_THINKING_BUDGET`` 等；本模块仅在未设置对应扁平环境变量时回填 ``OPENAI_*``、
``AGENT_CONTEXT_WINDOW``，并把 JSON 中的 thinking 元数据写入进程内补丁。
``get_default_model_config`` 对 thinking 的合并以 **环境变量优先于补丁**。

不在仓库中存放密钥；路径与文件名由用户自管。将 ``apiKey`` 写入 ``os.environ`` 的安全含义见
``docs/SECURITY.md`` §「外部 JSON（MINIAGENT_CONFIG）与进程环境」。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from miniagent.infrastructure.logger import get_logger

_logger = get_logger(__name__)

_PATCH: dict[str, Any] = {}


@dataclass
class ExternalConfigPatch:
    """合并进 get_default_model_config / 进程环境的非敏感解析结果。

    ``fallbacks`` 对应外部 JSON 中 ``model.fallbacks``：当前不触发自动换模重试，仅写入补丁元数据供后续扩展。
    """

    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    context_window: int | None = None
    thinking_default: str | None = None
    thinking_budget_by_model: dict[str, int] = field(default_factory=dict)
    fallbacks: list[str] = field(default_factory=list)


def get_external_config_patch() -> dict[str, Any]:
    """进程内已加载的补丁（空 dict 表示未加载或失败）。"""
    return dict(_PATCH)


def reset_external_config_for_tests() -> None:
    """清空进程内 ``MINIAGENT_CONFIG`` 补丁缓存（pytest 隔离用）。"""
    global _PATCH
    _PATCH = {}


def _parse_primary_model(ref: str) -> tuple[str, str]:
    """bailian/qwen3.6-plus -> (bailian, qwen3.6-plus)"""
    ref = (ref or "").strip()
    if "/" in ref:
        prov, mid = ref.split("/", 1)
        return prov.strip(), mid.strip()
    return "", ref


def _models_from_provider(p: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """解析单个 provider 下的 models（list 或 id->meta dict）。"""
    out: list[tuple[str, dict[str, Any]]] = []
    raw = p.get("models")
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            mid = str(item.get("id") or item.get("name") or item.get("model") or "").strip()
            if mid:
                out.append((mid, item))
    elif isinstance(raw, dict):
        for mid_key, item in raw.items():
            mid = str(mid_key).strip()
            if not mid:
                continue
            meta = item if isinstance(item, dict) else {}
            out.append((mid, meta))
    return out


def _merge_model_limits_from_providers(providers: dict[str, Any]) -> dict[str, dict[str, int]]:
    """model_id -> {context_window?, max_tokens?}（字段名兼容常见外部 JSON 约定）。"""
    limits: dict[str, dict[str, int]] = {}
    for _pid, p in providers.items():
        if not isinstance(p, dict):
            continue
        for mid, item in _models_from_provider(p):
            slot = limits.setdefault(mid, {})
            cw = item.get("contextWindow") or item.get("context_window")
            if isinstance(cw, int) and cw > 0:
                slot["context_window"] = cw
            mt = item.get("maxTokens") or item.get("max_tokens")
            if isinstance(mt, int) and mt > 0:
                slot["max_tokens"] = mt
    return limits


def _find_provider_dict_for_model(
    providers: dict[str, Any], model_id: str
) -> dict[str, Any] | None:
    """当 primary 无 provider/ 前缀时，按 models 列表反查所属 provider。"""
    for _pid, p in providers.items():
        if not isinstance(p, dict):
            continue
        for mid, _ in _models_from_provider(p):
            if mid == model_id:
                return p
    return None


def load_external_config_from_env() -> ExternalConfigPatch:
    """读取可选 JSON 路径（``MINIAGENT_CONFIG`` / ``MINIAGENT_OPENCLAW_CONFIG``）。

    非主配置方式：日常请用 ``.env``。若设置了路径且文件存在，则仅当对应 ``os.environ``
    键未设置时写入 ``OPENAI_*`` / ``AGENT_CONTEXT_WINDOW``；thinking 类仍以
    ``AGENT_THINKING_DEFAULT`` / ``OPENAI_THINKING_BUDGET`` 等为优先（见 ``get_default_model_config``）。
    """
    global _PATCH
    path = (os.environ.get("MINIAGENT_CONFIG") or os.environ.get("MINIAGENT_OPENCLAW_CONFIG") or "").strip()
    patch = ExternalConfigPatch()
    if not path or not os.path.isfile(path):
        _PATCH = {}
        return patch

    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        _logger.warning("MINIAGENT_CONFIG 读取失败 %s: %s", path, e)
        _PATCH = {}
        return patch

    models = raw.get("models") or {}
    providers = (models.get("providers") or {}) if isinstance(models, dict) else {}
    agents = raw.get("agents") or {}
    defaults = (agents.get("defaults") or {}) if isinstance(agents, dict) else {}

    primary_ref = ""
    mdl = defaults.get("model") if isinstance(defaults.get("model"), dict) else {}
    if isinstance(mdl, dict):
        primary_ref = str(mdl.get("primary") or "").strip()
        patch.fallbacks = [str(x) for x in (mdl.get("fallbacks") or []) if x]

    model_limits = _merge_model_limits_from_providers(providers)

    prov_id, model_id = _parse_primary_model(primary_ref)
    if model_id and not prov_id:
        p_auto = _find_provider_dict_for_model(providers, model_id)
        if isinstance(p_auto, dict):
            bu = str(p_auto.get("baseUrl") or p_auto.get("base_url") or "").strip()
            if bu:
                patch.base_url = bu
            ak = str(p_auto.get("apiKey") or p_auto.get("api_key") or "").strip()
            if ak:
                patch.api_key = ak
    elif prov_id and isinstance(providers.get(prov_id), dict):
        p = providers[prov_id]
        bu = str(p.get("baseUrl") or p.get("base_url") or "").strip()
        if bu:
            patch.base_url = bu
        ak = str(p.get("apiKey") or p.get("api_key") or "").strip()
        if ak:
            patch.api_key = ak
    if model_id:
        patch.model = model_id

    if isinstance(defaults.get("contextTokens"), int):
        patch.context_window = int(defaults["contextTokens"])
    td = defaults.get("thinkingDefault")
    if isinstance(td, str) and td.strip():
        patch.thinking_default = td.strip().lower()

    per_models = defaults.get("models") if isinstance(defaults.get("models"), dict) else {}
    for ref, meta in per_models.items():
        if not isinstance(meta, dict):
            continue
        params = meta.get("params") if isinstance(meta.get("params"), dict) else {}
        tb = params.get("thinking_budget")
        if isinstance(tb, int) and tb >= 0:
            _, mid = _parse_primary_model(str(ref))
            key = mid or str(ref)
            patch.thinking_budget_by_model[key] = tb

    # 应用：环境未设置时才写入
    if patch.base_url and not os.environ.get("OPENAI_BASE_URL"):
        os.environ["OPENAI_BASE_URL"] = patch.base_url
    if patch.model and not os.environ.get("OPENAI_MODEL"):
        os.environ["OPENAI_MODEL"] = patch.model
    if patch.api_key and not os.environ.get("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = patch.api_key
    if patch.context_window is not None and not os.environ.get("AGENT_CONTEXT_WINDOW"):
        os.environ["AGENT_CONTEXT_WINDOW"] = str(patch.context_window)

    _PATCH = {
        "thinking_default": patch.thinking_default,
        "thinking_budget_by_model": dict(patch.thinking_budget_by_model),
        "fallbacks": list(patch.fallbacks),
        "primary_ref": primary_ref,
        "model_limits": model_limits,
    }
    _logger.info("已加载外部配置: %s (model=%s)", path, patch.model or os.environ.get("OPENAI_MODEL"))
    return patch


__all__ = [
    "ExternalConfigPatch",
    "load_external_config_from_env",
    "get_external_config_patch",
    "reset_external_config_for_tests",
]
