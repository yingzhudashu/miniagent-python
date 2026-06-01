"""Mini Agent Python — JSON配置加载器

提供分层配置加载机制：
1. config.defaults.json - 默认配置（随代码发布）
2. config.user.json - 用户配置（覆盖默认值）
3. 环境变量 - 运行时覆盖（最高优先级）

优先级顺序（从低到高）：
defaults → user → 环境变量

敏感信息（API密钥等）保留在 .env 文件中，不在JSON配置中管理。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class JsonConfigLoader:
    """JSON配置加载器。

    支持分层加载、点路径访问、环境变量覆盖。

    Example:
        config = JsonConfigLoader()
        model = config.get("model.model", "gpt-4o-mini")
        temperature = config.get("model.temperature", 0.7)
    """

    _instance: JsonConfigLoader | None = None
    _defaults_path: str = ""
    _user_path: str = ""

    def __init__(
        self,
        defaults_path: str | None = None,
        user_path: str | None = None,
    ) -> None:
        """初始化配置加载器。

        Args:
            defaults_path: 默认配置文件路径（None时使用内置路径）
            user_path: 用户配置文件路径（None时自动查找）
        """
        # 确定配置文件路径
        if defaults_path is None:
            # 使用内置默认配置
            self._defaults_path = str(
                Path(__file__).parent.parent / "config.defaults.json"
            )
        else:
            self._defaults_path = defaults_path

        if user_path is None:
            # 自动查找用户配置
            state_dir = os.environ.get("MINI_AGENT_STATE", "workspaces")
            self._user_path = os.path.join(state_dir, "config.user.json")
        else:
            self._user_path = user_path

        self._defaults: dict[str, Any] = {}
        self._user: dict[str, Any] = {}
        self._loaded = False

    def _load(self) -> None:
        """加载配置文件（延迟加载）。"""
        if self._loaded:
            return

        # 加载默认配置
        self._defaults = self._load_json(self._defaults_path)

        # 加载用户配置（可选）
        if os.path.isfile(self._user_path):
            self._user = self._load_json(self._user_path)

        self._loaded = True

    def _load_json(self, path: str) -> dict[str, Any]:
        """加载JSON文件。

        Args:
            path: 文件路径

        Returns:
            解析后的字典，失败时返回空字典
        """
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
                # 过滤掉非配置字段（如 $schema, version, description）
                return {
                    k: v
                    for k, v in data.items()
                    if not k.startswith("$") and k not in ("version", "description")
                }
        except FileNotFoundError:
            return {}
        except json.JSONDecodeError as e:
            import logging
            _logger = logging.getLogger(__name__)
            _logger.warning("配置文件JSON解析失败: %s: %s", path, e)
            return {}

    def get(self, key: str, default: Any = None) -> Any:
        """获取配置值。

        支持点路径访问，如 "model.temperature"。
        优先级：环境变量 > 用户配置 > 默认配置。

        Args:
            key: 配置键（点路径格式）
            default: 默认值（未找到时返回）

        Returns:
            配置值
        """
        self._load()

        # 1. 检查环境变量（最高优先级）
        env_key = self._to_env_key(key)
        env_value = os.environ.get(env_key)
        if env_value is not None:
            return self._parse_env_value(env_value, default)

        # 2. 检查用户配置
        user_value = self._get_nested(self._user, key)
        if user_value is not None:
            return user_value

        # 3. 检查默认配置
        defaults_value = self._get_nested(self._defaults, key)
        if defaults_value is not None:
            return defaults_value

        return default

    def _to_env_key(self, key: str) -> str:
        """将点路径转换为环境变量名。

        Args:
            key: 点路径（如 "model.temperature"）

        Returns:
            环境变量名（如 "MINIAGENT_MODEL_TEMPERATURE"）
        """
        parts = key.split(".")
        return "MINIAGENT_" + "_".join(p.upper() for p in parts)

    def _parse_env_value(self, value: str, default: Any) -> Any:
        """解析环境变量值，尝试转换为合适的类型。

        Args:
            value: 环境变量字符串
            default: 默认值（用于类型推断）

        Returns:
            解析后的值
        """
        # 根据默认值类型推断
        if isinstance(default, bool):
            return value.lower() in ("1", "true", "yes", "on")
        if isinstance(default, int):
            try:
                return int(value)
            except ValueError:
                return value
        if isinstance(default, float):
            try:
                return float(value)
            except ValueError:
                return value
        # 字符串或其他类型
        return value

    def _get_nested(self, data: dict[str, Any], key: str) -> Any:
        """获取嵌套字典中的值。

        Args:
            data: 字典
            key: 点路径（如 "model.temperature"）

        Returns:
            找到的值，或 None
        """
        parts = key.split(".")
        current = data
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current

    def get_section(self, section: str) -> dict[str, Any]:
        """获取整个配置部分。

        Args:
            section: 配置部分名称（如 "model"）

        Returns:
            配置字典（合并默认值、用户值、环境变量）
        """
        self._load()

        # 获取默认配置部分
        defaults_section = self._defaults.get(section, {})

        # 获取用户配置部分
        user_section = self._user.get(section, {})

        # 合并（用户覆盖默认）
        merged = {**defaults_section, **user_section}

        # 应用环境变量覆盖
        for key, default_value in merged.items():
            full_key = f"{section}.{key}"
            env_key = self._to_env_key(full_key)
            env_value = os.environ.get(env_key)
            if env_value is not None:
                merged[key] = self._parse_env_value(env_value, default_value)

        return merged

    def reload(self) -> None:
        """重新加载配置文件。"""
        self._loaded = False
        self._load()

    @classmethod
    def get_instance(cls) -> JsonConfigLoader:
        """获取全局配置加载器实例（单例）。"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance


# 全局便捷函数
def get_config(key: str, default: Any = None) -> Any:
    """获取配置值（便捷函数）。

    Args:
        key: 配置键（点路径格式）
        default: 默认值

    Returns:
        配置值
    """
    return JsonConfigLoader.get_instance().get(key, default)


def get_config_section(section: str) -> dict[str, Any]:
    """获取配置部分（便捷函数）。

    Args:
        section: 配置部分名称

    Returns:
        配置字典
    """
    return JsonConfigLoader.get_instance().get_section(section)


def reload_config() -> None:
    """重新加载配置文件。"""
    JsonConfigLoader.get_instance().reload()


__all__ = [
    "JsonConfigLoader",
    "get_config",
    "get_config_section",
    "reload_config",
]