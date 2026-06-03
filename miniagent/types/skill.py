"""Mini Agent Python — 技能系统与 ClawHub 类型

技能系统是 Mini Agent 的模块化扩展机制，包含：
- Skill: 单个可复用的能力单元
- SkillPackage: 一组相关技能的集合
- SkillRegistryProtocol: 技能注册表
- ClawHub: 技能市场（搜索/下载）

**Protocol 最佳实践**：
- Protocol 不使用 @abstractmethod（Python Protocol 仅定义方法签名）
- 使用 @runtime_checkable 支持 isinstance() 检查
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from miniagent.types.config import AgentConfig
from miniagent.types.tool import Toolbox, ToolDefinition


@dataclass
class SkillMetadata:
    """技能元数据（gating 信息）

    Attributes:
        bins: 必需的系统二进制文件
        com: 必需的 Windows COM ProgID（如 ``Mathcad.Application``）
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
    com: list[str] | None = None
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
    tools: dict[str, ToolDefinition] = field(default_factory=dict)
    toolboxes: list[Toolbox] = field(default_factory=list)
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
    scope: str = "global"  # "global" | "session:<session_id>"


@runtime_checkable
class SkillRegistryProtocol(Protocol):
    """技能注册表接口协议

    管理技能的注册、注销、查询、技能包管理、贡献合并。

    该 Protocol 用于 ``miniagent.runtime.context.RuntimeContext`` 的
    skill_registry 字段类型，支持依赖注入模式。
    """

    def register(self, skill: Skill) -> None:
        """注册一个技能

        Args:
            skill: 技能对象
        """
        ...

    def unregister(self, skill_id: str) -> bool:
        """注销一个技能

        Args:
            skill_id: 技能 ID

        Returns:
            是否成功注销
        """
        ...

    def get(self, skill_id: str) -> Skill | None:
        """查询单个技能

        Args:
            skill_id: 技能 ID

        Returns:
            技能对象，若不存在则返回 None
        """
        ...

    def get_all(self) -> list[Skill]:
        """获取所有技能

        Returns:
            技能列表
        """
        ...

    def get_packages(self) -> list[SkillPackage]:
        """获取所有技能包

        Returns:
            技能包列表
        """
        ...

    def register_package(self, pkg: SkillPackage) -> None:
        """注册一个技能包

        Args:
            pkg: 技能包对象
        """
        ...

    def get_package(self, package_id: str) -> SkillPackage | None:
        """按包 ID 查询技能包

        Args:
            package_id: 技能包 ID

        Returns:
            技能包对象，若不存在则返回 None
        """
        ...

    def unregister_package(self, package_id: str) -> tuple[list[str], list[str]]:
        """注销技能包

        Args:
            package_id: 技能包 ID

        Returns:
            (移除的技能 ID 列表, 移除的工具名称列表)
        """
        ...

    def clear_packages(self) -> tuple[list[str], list[str]]:
        """清空所有技能包

        Returns:
            (移除的技能 ID 列表, 移除的工具名称列表)
        """
        ...

    def get_all_toolboxes(self, config: AgentConfig | None = None) -> list[Toolbox]:
        """获取可用技能贡献的工具箱（经 gating 过滤）

        Args:
            config: Agent 配置（可选）

        Returns:
            工具箱列表
        """
        ...

    def get_all_tools(self, config: AgentConfig | None = None) -> dict[str, ToolDefinition]:
        """获取可用技能贡献的工具（经 gating 过滤）

        Args:
            config: Agent 配置（可选）

        Returns:
            工具名称到工具定义的映射
        """
        ...

    def get_system_prompts(self, config: AgentConfig | None = None) -> list[str]:
        """获取可用技能的 system prompt 增强（经 gating 过滤）

        Args:
            config: Agent 配置（可选）

        Returns:
            system prompt 片段列表
        """
        ...

    def get_eligible_skills(self, config: AgentConfig | None = None) -> list[Skill]:
        """根据配置过滤后的可用技能

        Args:
            config: Agent 配置（可选）

        Returns:
            可用技能列表
        """
        ...

    def get_skill_entry(self, skill_id: str) -> SkillEntry | None:
        """获取技能配置覆盖

        Args:
            skill_id: 技能 ID

        Returns:
            技能配置覆盖，若不存在则返回 None
        """
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


@runtime_checkable
class ClawHubClientProtocol(Protocol):
    """ClawHub 客户端接口协议

    提供技能市场的搜索、详情查询、下载功能。

    该 Protocol 用于 ``miniagent.runtime.context.RuntimeContext`` 的
    clawhub 字段类型，支持依赖注入模式。
    """

    async def search(self, query: str, limit: int = 10) -> list[ClawHubSearchResult]:
        """搜索技能

        Args:
            query: 搜索关键词
            limit: 返回结果数量上限

        Returns:
            搜索结果列表
        """
        ...

    async def get_detail(self, slug: str) -> ClawHubSkillDetail:
        """获取技能详情

        Args:
            slug: 技能 slug

        Returns:
            技能详情
        """
        ...

    async def download(
        self,
        slug: str,
        version: str | None = None,
        *,
        skills_root: str | None = None,
    ) -> dict[str, Any]:
        """下载技能包

        Args:
            slug: 技能 slug
            version: 版本号（可选）
            skills_root: 技能根目录（可选）

        Returns:
            包含 'path' 和 'files' 键的字典
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
