"""Mini Agent Python — 技能系统与 ClawHub 类型

技能系统是 Mini Agent 的模块化扩展机制，包含：
- Skill: 单个可复用的能力单元
- SkillPackage: 一组相关技能的集合
- SkillRegistryProtocol: 技能注册表
- ClawHub: 技能市场（搜索/下载）
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Any, Protocol

from src.types.config import AgentConfig
from src.types.tool import ToolDefinition, Toolbox


@dataclass
class SkillMetadata:
    """技能元数据（gating 信息）

    Attributes:
        bins: 必需的系统二进制文件
        env: 必需的环境变量
        config: 必需的 AgentConfig 字段
        primary_env: 主环境变量名
        os: 适用操作系统
        always: 始终加载
        skill_key: 技能唯一键
        user_invocable: 用户可调用
        disable_model_invocation: 排除模型调用
    """

    bins: list[str] | None = None
    env: list[str] | None = None
    config: list[str] | None = None
    primary_env: str | None = None
    os: list[str] | None = None
    always: bool = False
    skill_key: str | None = None
    user_invocable: bool = True
    disable_model_invocation: bool = False


@dataclass
class SkillEntry:
    """技能配置覆盖

    Attributes:
        enabled: 是否启用
        env: 注入的环境变量
        api_key: API Key（可以是字符串或来源配置）
        config: 自定义配置
    """

    enabled: bool = True
    env: dict[str, str] = field(default_factory=dict)
    api_key: str | dict[str, str] | None = None
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class Skill:
    """技能：一个独立的、可复用的能力单元

    Attributes:
        id: 技能唯一标识
        name: 技能名称
        description: 技能描述
        keywords: 关键词，辅助 LLM 匹配
        tools: 贡献的工具定义
        toolboxes: 贡献的工具箱
        system_prompt: 追加到 system prompt 的指令
        skill_md: SKILL.md 原始内容
        metadata: 技能元数据（gating）
        source_path: 来源路径
    """

    id: str
    name: str
    description: str
    keywords: list[str] = field(default_factory=list)
    tools: dict[str, "ToolDefinition"] = field(default_factory=dict)
    toolboxes: list["Toolbox"] = field(default_factory=list)
    system_prompt: str | None = None
    skill_md: str | None = None
    metadata: SkillMetadata | None = None
    source_path: str | None = None


@dataclass
class SkillPackage:
    """技能包：一组相关技能的集合

    Attributes:
        id: 技能包唯一标识
        name: 技能包名称
        description: 技能包描述
        skills: 包含的技能列表
        skill_md: SKILL.md 原始内容
        source_path: 加载来源路径
    """

    id: str
    name: str
    description: str
    skills: list[Skill] = field(default_factory=list)
    skill_md: str | None = None
    source_path: str = ""


class SkillRegistryProtocol(Protocol):
    """技能注册表接口

    管理技能的注册、注销、查询、技能包管理、贡献合并。
    """

    @abstractmethod
    def register(self, skill: Skill) -> None:
        """注册一个技能"""
        ...

    @abstractmethod
    def unregister(self, skill_id: str) -> bool:
        """注销一个技能"""
        ...

    @abstractmethod
    def get(self, skill_id: str) -> Skill | None:
        """查询单个技能"""
        ...

    @abstractmethod
    def get_all(self) -> list[Skill]:
        """获取所有技能"""
        ...

    @abstractmethod
    def get_packages(self) -> list[SkillPackage]:
        """获取所有技能包"""
        ...

    @abstractmethod
    def register_package(self, pkg: SkillPackage) -> None:
        """注册一个技能包"""
        ...

    @abstractmethod
    def get_all_toolboxes(self) -> list[Toolbox]:
        """获取所有技能贡献的工具箱"""
        ...

    @abstractmethod
    def get_all_tools(self) -> dict[str, ToolDefinition]:
        """获取所有技能贡献的工具"""
        ...

    @abstractmethod
    def get_system_prompts(self) -> list[str]:
        """获取所有技能的 system prompt 增强"""
        ...

    @abstractmethod
    def get_eligible_skills(self, config: AgentConfig | None = None) -> list[Skill]:
        """根据配置过滤后的可用技能"""
        ...

    @abstractmethod
    def get_skill_entry(self, skill_id: str) -> SkillEntry | None:
        """获取技能配置覆盖"""
        ...


@dataclass
class ClawHubSearchResult:
    """ClawHub 技能搜索结果

    Attributes:
        slug: 技能 slug
        name: 技能名称
        description: 技能描述
        version: 当前版本
        tags: 标签
        downloads: 下载次数
        stars: 星标数
        author: 作者
    """

    slug: str
    name: str
    description: str
    version: str
    tags: list[str] = field(default_factory=list)
    downloads: int = 0
    stars: int = 0
    author: str = ""


@dataclass
class ClawHubSkillDetail:
    """ClawHub 技能详情

    Attributes:
        slug: 技能 slug
        name: 技能名称
        description: 技能描述
        version: 当前版本
        tags: 标签
        skill_md: SKILL.md 内容
        files: 技能文件列表
    """

    slug: str
    name: str
    description: str
    version: str
    tags: list[str] = field(default_factory=list)
    skill_md: str = ""
    files: list[dict[str, str]] = field(default_factory=list)


class ClawHubClientProtocol(Protocol):
    """ClawHub 客户端接口"""

    @abstractmethod
    async def search(self, query: str, limit: int = 10) -> list[ClawHubSearchResult]:
        """搜索技能"""
        ...

    @abstractmethod
    async def get_detail(self, slug: str) -> ClawHubSkillDetail:
        """获取技能详情"""
        ...

    @abstractmethod
    async def download(
        self, slug: str, version: str | None = None
    ) -> dict[str, Any]:
        """下载技能包

        Returns:
            dict with 'path' and 'files' keys
        """
        ...


__all__ = [
    "SkillMetadata",
    "SkillEntry",
    "Skill",
    "SkillPackage",
    "SkillRegistryProtocol",
    "ClawHubSearchResult",
    "ClawHubSkillDetail",
    "ClawHubClientProtocol",
]
