"""技能注册表扩展行为与 Protocol 合规测试。"""

from __future__ import annotations

from miniagent.skills.registry import DefaultSkillRegistry
from miniagent.types.config import AgentConfig
from miniagent.types.skill import (
    ClawHubClientProtocol,
    ClawHubSearchResult,
    ClawHubSkillDetail,
    Skill,
    SkillEntry,
    SkillMetadata,
    SkillPackage,
    SkillRegistryProtocol,
)
from miniagent.types.tool import ToolDefinition


def _minimal_tool(name: str) -> ToolDefinition:
    return ToolDefinition(
        schema={
            "type": "function",
            "function": {"name": name, "description": "t", "parameters": {"type": "object"}},
        },
        handler=lambda _a, _c: None,  # type: ignore[assignment]
        permission="sandbox",
        help_text="test",
    )


class TestProtocolCompliance:
    def test_registry_satisfies_protocol(self) -> None:
        reg = DefaultSkillRegistry()
        assert isinstance(reg, SkillRegistryProtocol)

    def test_clawhub_client_satisfies_protocol(self) -> None:
        from miniagent.skills.clawhub_client import create_clawhub_client

        client = create_clawhub_client()
        assert isinstance(client, ClawHubClientProtocol)


class TestSkillMetadataGating:
    def test_primary_env_api_key_satisfies_env_gate(self) -> None:
        reg = DefaultSkillRegistry()
        reg.set_skill_entries(
            {
                "my-skill": SkillEntry(api_key="sk-test-key"),
            }
        )
        reg.register(
            Skill(
                id="my-skill",
                name="T",
                description="d",
                metadata=SkillMetadata(env=["TAVILY_API_KEY"], primary_env="TAVILY_API_KEY"),
            )
        )
        eligible = reg.get_eligible_skills()
        assert any(s.id == "my-skill" for s in eligible)

    def test_skill_key_entry_lookup(self) -> None:
        reg = DefaultSkillRegistry()
        reg.set_skill_entries({"config-key": SkillEntry(enabled=False)})
        reg.register(
            Skill(
                id="runtime-id",
                name="T",
                description="d",
                metadata=SkillMetadata(skill_key="config-key"),
            )
        )
        entry = reg.get_skill_entry("runtime-id")
        assert entry is not None
        assert entry.enabled is False
        assert reg.get_eligible_skills() == []

    def test_entry_config_satisfies_config_gate(self) -> None:
        reg = DefaultSkillRegistry()
        reg.set_skill_entries({"s1": SkillEntry(config={"feature_x": True})})
        reg.register(
            Skill(
                id="s1",
                name="T",
                description="d",
                metadata=SkillMetadata(config=["feature_x"]),
            )
        )
        assert len(reg.get_eligible_skills(config=AgentConfig())) == 1

    def test_disable_model_invocation_excluded_from_tools(self) -> None:
        reg = DefaultSkillRegistry()
        reg.register(
            Skill(
                id="hidden",
                name="H",
                description="d",
                tools={"hidden_tool": _minimal_tool("hidden_tool")},
                metadata=SkillMetadata(disable_model_invocation=True),
            )
        )
        reg.register(
            Skill(
                id="visible",
                name="V",
                description="d",
                tools={"visible_tool": _minimal_tool("visible_tool")},
            )
        )
        assert "hidden_tool" not in reg.get_all_tools()
        assert "visible_tool" in reg.get_all_tools()
        assert len(reg.get_eligible_skills(for_model=False)) == 2

    def test_user_invocable_only_filter(self) -> None:
        reg = DefaultSkillRegistry()
        reg.register(
            Skill(
                id="internal",
                name="I",
                description="d",
                metadata=SkillMetadata(user_invocable=False),
            )
        )
        reg.register(Skill(id="public", name="P", description="d"))
        ids = {s.id for s in reg.get_eligible_skills(user_invocable_only=True)}
        assert ids == {"public"}


class TestScopeFiltering:
    def test_session_scoped_package_tools(self) -> None:
        reg = DefaultSkillRegistry()
        session_skill = Skill(
            id="sess-s1",
            name="S",
            description="d",
            tools={"sess_tool": _minimal_tool("sess_tool")},
        )
        global_skill = Skill(
            id="glob-s1",
            name="G",
            description="d",
            tools={"glob_tool": _minimal_tool("glob_tool")},
        )
        reg.register_package(
            SkillPackage(
                id="sess-pkg",
                name="SP",
                description="d",
                skills=[session_skill],
                scope="session:abc123",
            )
        )
        reg.register_package(
            SkillPackage(
                id="glob-pkg",
                name="GP",
                description="d",
                skills=[global_skill],
                scope="global",
            )
        )
        all_tools = reg.get_all_tools(session_key=None)
        assert "sess_tool" in all_tools
        assert "glob_tool" in all_tools

        scoped = reg.get_all_tools(session_key="cli:abc123")
        assert "glob_tool" in scoped
        assert "sess_tool" in scoped

        other = reg.get_all_tools(session_key="cli:other")
        assert "glob_tool" in other
        assert "sess_tool" not in other

    def test_orphan_skill_treated_as_global(self) -> None:
        reg = DefaultSkillRegistry()
        reg.register(
            Skill(
                id="orphan",
                name="O",
                description="d",
                tools={"orphan_tool": _minimal_tool("orphan_tool")},
            )
        )
        tools = reg.get_all_tools(session_key="cli:any")
        assert "orphan_tool" in tools


class TestClawHubMapping:
    def test_to_search_result(self) -> None:
        from miniagent.skills.clawhub_client import _to_search_result

        item = {
            "slug": "demo",
            "name": "Demo",
            "description": "A demo skill",
            "latestVersion": {"version": "1.2.3"},
            "tags": ["web"],
            "downloads": 42,
            "stars": 7,
            "author": "alice",
        }
        result = _to_search_result(item)
        assert isinstance(result, ClawHubSearchResult)
        assert result.slug == "demo"
        assert result.version == "1.2.3"
        assert result.downloads == 42

    def test_to_skill_detail(self) -> None:
        from miniagent.skills.clawhub_client import _to_skill_detail

        data = {
            "slug": "demo",
            "name": "Demo",
            "description": "desc",
            "latestVersion": {
                "version": "2.0.0",
                "skillMd": "# Demo",
                "files": [{"path": "SKILL.md", "content": "---\n"}],
            },
        }
        detail = _to_skill_detail(data, "demo")
        assert isinstance(detail, ClawHubSkillDetail)
        assert detail.skill_md == "# Demo"
        assert detail.files[0]["path"] == "SKILL.md"


class TestLoaderMetadataFields:
    def test_extended_metadata_parsing(self) -> None:
        from miniagent.skills.loader import _map_metadata

        meta = {
            "metadata": {
                "skill_key": "my-key",
                "user_invocable": False,
                "disable_model_invocation": True,
            }
        }
        result = _map_metadata(meta)
        assert result is not None
        assert result.skill_key == "my-key"
        assert result.user_invocable is False
        assert result.disable_model_invocation is True
