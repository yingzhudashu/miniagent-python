"""SKILL.md 元数据解析测试"""

from miniagent.skills.loader import _map_metadata, _resolve_base_dir, parse_skill_md


class TestParseSkillMdMultilineYaml:
    def test_single_line_kv(self):
        content = """---
name: test-skill
description: A simple test skill
keywords: test, demo
---

Body content here.
"""
        meta, body = parse_skill_md(content)
        assert meta["name"] == "test-skill"
        assert meta["description"] == "A simple test skill"
        # yaml.safe_load 解析逗号分隔的字符串，保持原始字符串值
        kw = meta["keywords"]
        if isinstance(kw, str):
            assert "test" in kw and "demo" in kw
        else:
            assert "test" in kw and "demo" in kw
        assert "Body content here" in body

    def test_multiline_folded_description(self):
        content = """---
name: multi-line-skill
description: |
  This is a multi-line
  description that spans
  multiple lines.
keywords:
  - keyword1
  - keyword2
---

Body text.
"""
        meta, body = parse_skill_md(content)
        assert meta["name"] == "multi-line-skill"
        assert "multi-line" in meta["description"]
        assert meta["keywords"] == ["keyword1", "keyword2"]

    def test_flat_metadata(self):
        content = """---
name: json-meta-skill
metadata:
  bins:
    - node
    - python
  env:
    - API_KEY
  primary_env: API_KEY
---

Body.
"""
        meta, body = parse_skill_md(content)
        assert meta["name"] == "json-meta-skill"
        assert isinstance(meta["metadata"], dict)
        assert meta["metadata"]["bins"] == ["node", "python"]

    def test_no_front_matter(self):
        content = "Just plain content, no YAML."
        meta, body = parse_skill_md(content)
        assert meta == {}
        assert body == content

    def test_fallback_on_invalid_yaml(self):
        content = """---
name: test
description: invalid: yaml: [broken
---

Body.
"""
        # yaml.safe_load may or may not fail depending on content
        # either way should return something usable
        meta, body = parse_skill_md(content)
        assert "name" in meta or meta == {}


class TestMapMetadata:
    def test_flat_format(self):
        meta = {
            "metadata": {
                "bins": ["python"],
                "env": ["MY_KEY"],
                "always": True,
                "os": ["linux", "darwin"],
            }
        }
        result = _map_metadata(meta)
        assert result is not None
        assert result.bins == ["python"]
        assert result.env == ["MY_KEY"]
        assert result.always is True
        assert "linux" in result.os
        assert "darwin" in result.os

    def test_no_metadata(self):
        meta = {"name": "simple-skill"}
        result = _map_metadata(meta)
        assert result is None

    def test_string_json_metadata(self):
        import json

        raw = {"bins": ["node"], "env": ["API_KEY"]}
        meta = {"metadata": json.dumps(raw)}
        result = _map_metadata(meta)
        assert result is not None
        assert result.bins == ["node"]
        assert result.env == ["API_KEY"]

    def test_primary_env_both_formats(self):
        # primary_env (下划线格式)
        meta1 = {"metadata": {"primary_env": "KEY1"}}
        result1 = _map_metadata(meta1)
        assert result1 is not None
        assert result1.primary_env == "KEY1"

        # primaryEnv (驼峰格式)
        meta2 = {"metadata": {"primaryEnv": "KEY2"}}
        result2 = _map_metadata(meta2)
        assert result2 is not None
        assert result2.primary_env == "KEY2"


class TestResolveBaseDir:
    def test_replaces_placeholder(self):
        content = "Run: node {baseDir}/scripts/search.mjs"
        result = _resolve_base_dir(content, "/path/to/skill")
        assert "/path/to/skill/scripts/search.mjs" in result
        assert "{baseDir}" not in result

    def test_no_placeholder(self):
        content = "No placeholder here"
        result = _resolve_base_dir(content, "/some/path")
        assert result == content

    def test_uses_posix_slashes(self):
        content = "node {baseDir}/scripts/run.js"
        result = _resolve_base_dir(content, "D:\\skills\\my-skill")
        assert "D:/skills/my-skill/scripts/run.js" in result
        assert "\\" not in result.split("node ")[1]