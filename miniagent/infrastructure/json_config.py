"""Mini Agent Python — JSON配置加载器

提供分层配置加载机制：
1. config.defaults.json - 默认配置（随代码发布，含 User/Advanced 分层展示）
2. config.user.json - 用户配置（覆盖默认值）

优先级顺序：defaults → user

敏感信息（API密钥等）放在 config.user.json 的 secrets 部分，
由 env_loader.py 加载到环境变量供第三方 SDK 使用。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from miniagent.infrastructure.env_parse import FALSY, TRUTHY

_logger = logging.getLogger(__name__)

_METADATA_KEYS = frozenset({"version", "description"})
_METADATA_PREFIX = "_"


def _is_config_key(key: str) -> bool:
    """判断顶层键是否为运行时配置节（排除元数据）。"""
    if key.startswith(_METADATA_PREFIX) or key.startswith("$"):
        return False
    return key not in _METADATA_KEYS


class JsonConfigLoader:
    """JSON配置加载器。

    支持分层加载、点路径访问。仅合并 defaults 与 user 两层。
    """

    _instance: JsonConfigLoader | None = None
    _defaults_path: str = ""
    _user_path: str = ""

    def __init__(
        self,
        defaults_path: str | None = None,
        user_path: str | None = None,
    ) -> None:
        """创建加载器；路径省略时使用仓库根目录下的默认/用户配置文件。"""
        if defaults_path is None:
            self._defaults_path = str(
                Path(__file__).parent.parent.parent / "config.defaults.json"
            )
        else:
            self._defaults_path = defaults_path

        if user_path is None:
            self._user_path = str(Path(__file__).parent.parent.parent / "config.user.json")
        else:
            self._user_path = user_path

        self._defaults: dict[str, Any] = {}
        self._user: dict[str, Any] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return

        self._defaults = self._load_json(self._defaults_path)

        if os.path.isfile(self._user_path):
            self._user = self._load_json(self._user_path)

        self._loaded = True

    def _load_json(self, path: str) -> dict[str, Any]:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
                return {k: v for k, v in data.items() if _is_config_key(k)}
        except FileNotFoundError:
            return {}
        except json.JSONDecodeError as e:
            _logger.warning("配置文件JSON解析失败: %s: %s", path, e)
            return {}

    def get(self, key: str, default: Any = None) -> Any:
        """获取配置值。支持点路径，如 ``model.temperature``。"""
        self._load()

        user_value = self._get_nested(self._user, key)
        if user_value is not None:
            return user_value

        defaults_value = self._get_nested(self._defaults, key)
        if defaults_value is not None:
            return defaults_value

        return default

    def _get_nested(self, data: dict[str, Any], key: str) -> Any:
        parts = key.split(".")
        current: Any = data
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current

    def get_section(self, section: str) -> dict[str, Any]:
        """获取整个配置部分（defaults 与 user 浅合并）。"""
        self._load()

        defaults_section = self._defaults.get(section, {})
        user_section = self._user.get(section, {})
        if not isinstance(defaults_section, dict):
            defaults_section = {}
        if not isinstance(user_section, dict):
            user_section = {}

        merged = {**defaults_section, **user_section}
        return {k: v for k, v in merged.items() if k != "description"}

    def get_section_runtime(self, section: str) -> dict[str, Any]:
        """同 :meth:`get_section`（保留别名，兼容旧调用）。"""
        return self.get_section(section)

    def reload(self) -> None:
        """丢弃内存缓存并从磁盘重新加载 defaults 与 user 配置。"""
        self._loaded = False
        self._load()

    @classmethod
    def get_instance(cls) -> JsonConfigLoader:
        """返回进程级单例加载器（懒创建）。"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance


def get_config(key: str, default: Any = None) -> Any:
    """读取配置项；支持 ``section.key`` 点路径，user 覆盖 defaults。"""
    return JsonConfigLoader.get_instance().get(key, default)


def get_config_bool(key: str, default: bool = False) -> bool:
    """读取布尔配置项，兼容 JSON bool、数字及常见字符串真值/假值。"""
    value = get_config(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        s = value.strip().lower()
        if s in TRUTHY:
            return True
        if s in FALSY:
            return False
        return default
    if value is None:
        return default
    return default


def get_config_section(section: str) -> dict[str, Any]:
    """读取顶层配置节（defaults 与 user 浅合并）。"""
    return JsonConfigLoader.get_instance().get_section(section)


def reload_config() -> None:
    """重新加载 JSON 配置（不刷新 secrets 环境变量或 LLM 客户端）。"""
    JsonConfigLoader.get_instance().reload()


def reload_runtime_config() -> None:
    """重新加载 JSON 配置，并同步 secrets 环境变量与 LLM 客户端缓存。"""
    reload_config()
    from miniagent.core.openai_client import sync_runtime_context_openai_client
    from miniagent.infrastructure.env_loader import load_secrets_from_project_root

    load_secrets_from_project_root()
    sync_runtime_context_openai_client()


def get_user_config_path() -> Path:
    """返回当前 ``config.user.json`` 路径（与 :class:`JsonConfigLoader` 一致）。"""
    loader = JsonConfigLoader.get_instance()
    return Path(loader._user_path)


__all__ = [
    "JsonConfigLoader",
    "get_config",
    "get_config_bool",
    "get_config_section",
    "get_user_config_path",
    "reload_config",
    "reload_runtime_config",
]
