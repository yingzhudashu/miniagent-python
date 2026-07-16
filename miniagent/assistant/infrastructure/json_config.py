"""Mini Agent Python — JSON配置加载器

提供分层配置加载机制：
1. miniagent/resources/config.defaults.json - 随 wheel 发布的默认配置
2. config.user.json - 用户配置（覆盖默认值）

优先级顺序：defaults → user

敏感信息（API密钥等）放在 config.user.json 的 secrets 部分，
由 env_loader.py 加载到环境变量供第三方 SDK 使用。
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from copy import deepcopy
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from miniagent.assistant.bootstrap.application import ApplicationContainer
    from miniagent.assistant.contracts.configuration import ConfigSnapshot

_logger = logging.getLogger(__name__)

_METADATA_KEYS = frozenset({"version", "description"})
_METADATA_PREFIX = "_"


def _packaged_defaults_path() -> str:
    """Return the default configuration bundled in the installed package."""
    return str(files("miniagent.assistant.resources").joinpath("config.defaults.json"))


def _resolve_defaults_path() -> str:
    """Return the single default configuration bundled with the package."""
    return _packaged_defaults_path()


def _is_config_key(key: str) -> bool:
    """判断顶层键是否为运行时配置节（排除元数据）。"""
    if key.startswith(_METADATA_PREFIX) or key.startswith("$"):
        return False
    return key not in _METADATA_KEYS


class JsonConfigLoader:
    """JSON配置加载器。

    支持分层加载、点路径访问。仅合并 defaults 与 user 两层。
    """

    _defaults_path: str = ""
    _user_path: str = ""

    def __init__(
        self,
        defaults_path: str | None = None,
        user_path: str | None = None,
    ) -> None:
        """创建加载器；默认配置来自包资源，用户配置来自项目根。"""
        if defaults_path is None:
            self._defaults_path = _resolve_defaults_path()
        else:
            self._defaults_path = defaults_path

        if user_path is None:
            self._user_path = str(
                Path(__file__).parent.parent.parent.parent / "config.user.json"
            )
        else:
            self._user_path = user_path

        self._defaults: dict[str, Any] = {}
        self._user: dict[str, Any] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        self._defaults, self._user = self._read_layers(strict=False)
        self._loaded = True

    def _read_layers(self, *, strict: bool) -> tuple[dict[str, Any], dict[str, Any]]:
        defaults = self._load_json(self._defaults_path, strict=strict)
        user = (
            self._load_json(self._user_path, strict=strict)
            if os.path.isfile(self._user_path)
            else {}
        )
        if strict:
            _validate_user_keys(defaults, user)
        return defaults, user

    def _load_json(self, path: str, *, strict: bool = False) -> dict[str, Any]:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
                return {k: v for k, v in data.items() if _is_config_key(k)}
        except FileNotFoundError:
            return {}
        except json.JSONDecodeError as e:
            if strict:
                raise ValueError(f"配置文件 JSON 解析失败: {path}: {e}") from e
            _logger.warning("配置文件JSON解析失败: %s: %s", path, e)
            return {}

    def get(self, key: str, default: Any = None) -> Any:
        """获取配置值。支持点路径，如 ``llm.roles.default``。"""
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

    def reload(self, *, strict: bool = False) -> None:
        """Reload both layers, committing only after all files parse successfully."""
        defaults, user = self._read_layers(strict=strict)
        self._defaults = defaults
        self._user = user
        self._loaded = True

    def reloaded_copy(self, *, strict: bool = False) -> JsonConfigLoader:
        """Return a fresh loader for the same paths without mutating this instance."""
        candidate = JsonConfigLoader(self._defaults_path, self._user_path)
        candidate.reload(strict=strict)
        return candidate

    def with_runtime_overrides(self, overrides: dict[str, Any]) -> JsonConfigLoader:
        """Return an in-memory overlay without writing either configuration file.

        Top-level mapping sections are shallow-merged, matching
        :meth:`get_section`; scalar sections are replaced. Deep copies prevent
        callers from mutating this loader or the returned candidate through a
        shared nested object. This is intended for isolated harnesses and
        embedded runtimes, not persistent user configuration changes.
        """
        self._load()
        candidate = JsonConfigLoader(self._defaults_path, self._user_path)
        candidate._defaults = deepcopy(self._defaults)
        candidate._user = deepcopy(self._user)
        for section, value in overrides.items():
            current = candidate._user.get(section)
            if isinstance(current, dict) and isinstance(value, dict):
                candidate._user[section] = {**current, **deepcopy(value)}
            else:
                candidate._user[section] = deepcopy(value)
        candidate._loaded = True
        return candidate

    def get_user_section(self, section: str) -> dict[str, Any]:
        """Return one user-only section without values inherited from defaults."""
        self._load()
        value = self._user.get(section, {})
        return dict(value) if isinstance(value, dict) else {}

    def snapshot(self) -> ConfigSnapshot:
        """返回 defaults 与 user 深度合并后的不可变配置快照。"""
        from miniagent.assistant.contracts.configuration import ConfigSnapshot

        defaults, user = self._read_layers(strict=True)
        self._defaults = defaults
        self._user = user
        self._loaded = True
        return ConfigSnapshot(_deep_merge(self._defaults, self._user))

    @property
    def paths(self) -> tuple[Path, Path]:
        """Return the configured defaults and user file paths."""
        return Path(self._defaults_path), Path(self._user_path)


class ConfigurationService:
    """Single owner for the effective loader and immutable configuration snapshot."""

    def __init__(self, loader: JsonConfigLoader | None = None) -> None:
        self.loader = loader or JsonConfigLoader()
        self._snapshot: ConfigSnapshot | None = None

    def get(self, path: str, default: Any = None) -> Any:
        value = self.snapshot().get_path(path, default)
        return default if value is None else value

    def get_section(self, name: str) -> dict[str, Any]:
        value = self.snapshot().get_path(name, {})
        return _thaw_mapping(value) if isinstance(value, Mapping) else {}

    def get_user_section(self, name: str) -> dict[str, Any]:
        return self.loader.get_user_section(name)

    @property
    def paths(self) -> tuple[Path, Path]:
        return self.loader.paths

    def snapshot(self) -> ConfigSnapshot:
        if self._snapshot is None:
            self._snapshot = self.loader.snapshot()
        return self._snapshot

    def reload(self, *, strict: bool = False) -> None:
        self.loader.reload(strict=strict)
        self._snapshot = None

    def reloaded(self, *, strict: bool = True) -> ConfigurationService:
        return ConfigurationService(self.loader.reloaded_copy(strict=strict))


_configuration_service = ConfigurationService()


def _thaw_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    def thaw(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {str(key): thaw(child) for key, child in item.items()}
        if isinstance(item, tuple):
            return [thaw(child) for child in item]
        return item

    return {str(key): thaw(item) for key, item in value.items()}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """递归合并配置树，同时断开所有可变对象引用。"""
    merged = deepcopy(base)
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _validate_user_keys(defaults: dict[str, Any], user: dict[str, Any], prefix: str = "") -> None:
    """严格校验用户配置键，并在错误中报告完整点路径。"""
    if prefix.endswith(
        (".headers", ".options", ".compatibility", ".pricing", ".defaults")
    ):
        return
    dynamic_template = None
    if prefix == "llm.providers":
        dynamic_template = defaults.get("openai")
    elif prefix == "llm.models":
        dynamic_template = defaults.get("primary")
    elif prefix == "secrets.llm":
        dynamic_template = defaults.get("openai")
    for key, value in user.items():
        path = f"{prefix}.{key}" if prefix else key
        if key in defaults:
            default_value = defaults[key]
        elif isinstance(dynamic_template, dict):
            default_value = dynamic_template
        else:
            raise ValueError(f"未知配置项: {path}")
        if isinstance(value, dict):
            if not isinstance(default_value, dict):
                raise ValueError(f"配置项类型错误: {path} 应为 {type(default_value).__name__}")
            _validate_user_keys(default_value, value, path)
        elif isinstance(default_value, dict):
            raise ValueError(f"配置项类型错误: {path} 应为 object")
        elif default_value is not None and not _compatible_config_type(default_value, value):
            raise ValueError(
                f"配置项类型错误: {path} 应为 {type(default_value).__name__}，"
                f"实际为 {type(value).__name__}"
            )


def _compatible_config_type(default: Any, value: Any) -> bool:
    """判断用户值是否与默认值类型兼容，避免 bool 被当作整数。"""
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, float):
        return isinstance(value, int | float) and not isinstance(value, bool)
    if isinstance(default, int):
        return isinstance(value, int | float) and not isinstance(value, bool)
    return isinstance(value, type(default))


def get_config_snapshot() -> ConfigSnapshot:
    """Return the immutable snapshot owned by the active configuration service."""
    return _configuration_service.snapshot()


def get_configuration_service() -> ConfigurationService:
    """Return the single configuration owner used by this application process."""
    return _configuration_service


def install_configuration_service(service: ConfigurationService) -> None:
    """Install an isolated configuration owner."""
    global _configuration_service
    _configuration_service = service


def install_config_loader(loader: JsonConfigLoader) -> None:
    """Install a loader as the active configuration service."""
    install_configuration_service(ConfigurationService(loader))


def reset_config_loader() -> None:
    """Restore the default package/user loader after an isolated scope."""
    install_config_loader(JsonConfigLoader())


def get_config(key: str, default: Any = None) -> Any:
    """读取配置项；支持 ``section.key`` 点路径，user 覆盖 defaults。"""
    return _configuration_service.get(key, default)


def get_config_bool(key: str, default: bool = False) -> bool:
    """Read a native JSON boolean; other types are rejected by strict loading."""
    value = get_config(key, default)
    return value if isinstance(value, bool) else default


def get_config_section(section: str) -> dict[str, Any]:
    """读取顶层配置节（defaults 与 user 浅合并）。"""
    return _configuration_service.get_section(section)


def get_user_config_section(section: str) -> dict[str, Any]:
    """Read a section from ``config.user.json`` without inherited defaults."""
    return _configuration_service.get_user_section(section)


def get_config_paths() -> tuple[Path, Path]:
    """Return the active defaults and user configuration paths."""
    return _configuration_service.paths


def reload_config() -> None:
    """重新加载 JSON 配置（不刷新 secrets 环境变量或 LLM 客户端）。"""
    _configuration_service.reload()


async def reload_runtime_config(container: ApplicationContainer) -> None:
    """Validate a candidate configuration, then atomically publish its LLM gateway."""
    candidate = _configuration_service.reloaded(strict=True)
    from miniagent.assistant.infrastructure.env_loader import load_secrets_from_project_root
    from miniagent.llm.factory import create_llm_gateway

    replacement = create_llm_gateway(
        candidate.get,
        cache_path=get_config_paths()[1].parent / "llm-model-catalog.json",
    )
    previous = container.llm_gateway
    install_configuration_service(candidate)
    load_secrets_from_project_root()
    container.config = candidate
    container.llm_gateway = replacement
    if previous is not None and previous is not replacement:
        container.retired_llm_gateways.append(previous)


def get_user_config_path() -> Path:
    """返回当前 ``config.user.json`` 路径（与 :class:`JsonConfigLoader` 一致）。"""
    return _configuration_service.paths[1]


__all__ = [
    "JsonConfigLoader",
    "ConfigurationService",
    "_packaged_defaults_path",
    "_resolve_defaults_path",
    "get_config",
    "get_config_bool",
    "get_configuration_service",
    "get_config_paths",
    "get_config_section",
    "get_config_snapshot",
    "get_user_config_section",
    "get_user_config_path",
    "install_config_loader",
    "install_configuration_service",
    "reload_config",
    "reload_runtime_config",
    "reset_config_loader",
]
