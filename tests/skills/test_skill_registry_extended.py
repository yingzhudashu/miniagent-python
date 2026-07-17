"""技能注册表扩展行为与 Protocol 合规测试。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from miniagent.agent.types.config import AgentConfig
from miniagent.agent.types.skill import (
    ClawHubClientProtocol,
    ClawHubSearchResult,
    ClawHubSkillDetail,
    Skill,
    SkillEntry,
    SkillMetadata,
    SkillPackage,
    SkillRegistryProtocol,
)
from miniagent.agent.types.tool import Toolbox, ToolDefinition
from miniagent.assistant.skills import registry as registry_module
from miniagent.assistant.skills.registry import DefaultSkillRegistry


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
        from miniagent.assistant.skills.clawhub_client import create_clawhub_client

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


class TestRegistryGateEdges:
    def test_lookup_and_api_key_resolution_sources(self, monkeypatch) -> None:
        config = SimpleNamespace(secrets={"token": "from-config"})
        assert registry_module._lookup_dotted_path(config, "secrets.token") == "from-config"
        assert registry_module._lookup_dotted_path(config, ".secrets.token") == "from-config"
        assert registry_module._lookup_dotted_path(config, "missing.value") is None
        assert registry_module._resolve_api_key(None, None) is None
        assert registry_module._resolve_api_key("  key  ", None) == "key"
        assert registry_module._resolve_api_key("  ", None) is None

        monkeypatch.setenv("SKILL_GATE_KEY", "from-env")
        assert (
            registry_module._resolve_api_key({"env": "SKILL_GATE_KEY"}, None)
            == "from-env"
        )
        assert (
            registry_module._resolve_api_key({"source": "secrets.token"}, config)
            == "from-config"
        )

        monkeypatch.setattr(
            "miniagent.assistant.infrastructure.json_config.get_config",
            lambda path: "from-json" if path == "remote.key" else None,
        )
        assert (
            registry_module._resolve_api_key({"config": "remote.key"}, None)
            == "from-json"
        )
        monkeypatch.setattr(
            "miniagent.assistant.infrastructure.json_config.get_config",
            MagicMock(side_effect=RuntimeError("bad config")),
        )
        assert registry_module._resolve_api_key({"source": "bad"}, None) is None

    def test_environment_and_config_gate_variants(self, monkeypatch) -> None:
        entry = SkillEntry(
            env={"ENTRY_ENV": "value"},
            api_key={"env": "PRIMARY_KEY"},
            config={"entry_flag": True},
        )
        metadata = SkillMetadata(primary_env="PRIMARY_ENV")
        monkeypatch.setenv("OS_ENV", "yes")
        monkeypatch.setenv("PRIMARY_KEY", "secret")

        assert registry_module._env_satisfied("OS_ENV", None, None, None)
        assert registry_module._env_satisfied("ENTRY_ENV", entry, None, None)
        assert registry_module._env_satisfied("PRIMARY_ENV", entry, metadata, None)
        assert not registry_module._env_satisfied("MISSING", entry, metadata, None)
        assert registry_module._config_satisfied("entry_flag", entry, None)
        assert not registry_module._config_satisfied("missing", None, None)

        config = AgentConfig(debug=True)
        config.session_config.session_key = "session"
        assert registry_module._config_satisfied("debug", None, config)
        assert registry_module._config_satisfied(
            "session_config.session_key", None, config
        )
        assert not registry_module._config_satisfied("unknown.path", None, config)

    def test_gating_rejections_and_always_override(self, monkeypatch) -> None:
        monkeypatch.setattr(registry_module, "_is_bin_available", lambda name: name == "ok")
        monkeypatch.setattr(registry_module, "_is_com_available", lambda name: name == "ok")
        reg = DefaultSkillRegistry()
        skills = [
            Skill("always", "Always", "d", metadata=SkillMetadata(always=True, bins=["bad"])),
            Skill("wrong-os", "OS", "d", metadata=SkillMetadata(os=["never"])),
            Skill("bad-bin", "Bin", "d", metadata=SkillMetadata(bins=["bad"])),
            Skill("bad-com", "Com", "d", metadata=SkillMetadata(com=["bad"])),
            Skill("bad-env", "Env", "d", metadata=SkillMetadata(env=["MISSING_ENV"])),
            Skill("bad-config", "Config", "d", metadata=SkillMetadata(config=["missing"])),
            Skill("plain", "Plain", "d"),
        ]
        for skill in skills:
            reg.register(skill)
        reg.set_skill_entries({"plain": SkillEntry(enabled=False)})

        assert {skill.id for skill in reg.get_eligible_skills(AgentConfig())} == {"always"}

    def test_binary_and_com_availability_paths(self, monkeypatch) -> None:
        monkeypatch.setattr(registry_module.shutil, "which", lambda name: "/bin/x" if name == "x" else None)
        assert registry_module._is_bin_available("x")
        assert not registry_module._is_bin_available("missing")

        monkeypatch.setattr(registry_module.os, "name", "posix")
        assert not registry_module._is_com_available("App.Id")

    def test_package_cleanup_toolboxes_and_prompts(self) -> None:
        reg = DefaultSkillRegistry()
        first = Skill(
            "first",
            "First",
            "d",
            tools={"tool": _minimal_tool("tool")},
            toolboxes=[Toolbox("shared", "Shared", "d")],
            system_prompt=" prompt ",
            skill_md=None,
        )
        second = Skill(
            "second",
            "Second",
            "d",
            toolboxes=[
                Toolbox("shared", "Shared", "d"),
                Toolbox("unique", "Unique", "d"),
            ],
            system_prompt="  ",
        )
        reg.register_package(
            SkillPackage(
                "pkg",
                "Pkg",
                "d",
                skills=[first, second],
                scope="session:s1",
                skill_md="# package",
            )
        )
        assert first.skill_md == "# package"
        assert [box.id for box in reg.get_all_toolboxes(session_key="cli:s1")] == [
            "shared",
            "unique",
        ]
        assert reg.get_system_prompts(session_key="feishu:s1") == [" prompt "]
        assert reg.unregister("missing") is False
        assert reg.unregister_package("missing") == ([], [])

        removed_skills, removed_tools = reg.unregister_package("pkg")
        assert removed_skills == ["first", "second"]
        assert removed_tools == ["tool"]
        assert reg.get_packages() == []

        reg.register(first)
        assert reg.clear_packages() == (["first"], ["tool"])


class TestClawHubMapping:
    def test_to_search_result(self) -> None:
        from miniagent.assistant.skills.clawhub_client import _to_search_result

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
        from miniagent.assistant.skills.clawhub_client import _to_skill_detail

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
        from miniagent.assistant.skills.loader import _map_metadata

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
