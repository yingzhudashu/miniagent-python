"""技能系统模块（磁盘包发现、注册表、ClawHub、运行期 refresh）

技能包从 ``get_skills_root()`` 目录加载（见 ``paths``），在 ``engine.init_subsystems`` 中注册到
``DefaultSkillRegistry``，并与 ``builtin_toolboxes.BUILTIN_TOOLBOXES`` 合并后交给规划器。
``install_skill`` 与 ``.reload-skills`` 可在不重启进程时调用 ``refresh_skills`` 热加载。

ClawHub 客户端由 ``create_clawhub_client()`` 构造并注入 ``RuntimeContext.clawhub``，供工具层
搜索/安装技能时复用（``ToolContext.clawhub`` 优先）。

导出：注册表、解析/加载函数、ClawHub 工厂、技能根路径解析、refresh。

目录迁移与 wheel 发行说明见根目录 ``README``；第三方清单见 ``workspaces/skills/THIRD_PARTY_SKILLS.md``。
"""

from miniagent.skills.autovet import auto_vet_skill
from miniagent.skills.clawhub_client import (
    close_clawhub_client,
    create_clawhub_client,
    search_local_skills,
)
from miniagent.skills.loader import (
    discover_skill_packages,
    evict_skill_modules,
    load_skill_package,
    parse_skill_md,
)
from miniagent.skills.paths import (
    get_all_skill_roots,
    get_session_skills_dir,
    get_skills_root,
    resolve_scope_for_root,
)
from miniagent.skills.refresh import RefreshResult, refresh_skills
from miniagent.skills.registry import DefaultSkillRegistry

__all__ = [
    "DefaultSkillRegistry",
    "RefreshResult",
    "parse_skill_md",
    "load_skill_package",
    "discover_skill_packages",
    "refresh_skills",
    "create_clawhub_client",
    "close_clawhub_client",
    "search_local_skills",
    "get_skills_root",
    "get_session_skills_dir",
    "get_all_skill_roots",
    "resolve_scope_for_root",
    # 模块清理（测试用）
    "evict_skill_modules",
    # 自动审查
    "auto_vet_skill",
]
