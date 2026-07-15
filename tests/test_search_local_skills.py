"""search_local_skills 与 ClawHub 本地搜索测试。"""

from __future__ import annotations

import os
import tempfile

from miniagent.assistant.skills.clawhub_client import search_local_skills


def test_search_local_skills_multiline_description() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        pkg_dir = os.path.join(tmpdir, "multi-desc")
        os.makedirs(pkg_dir)
        with open(os.path.join(pkg_dir, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(
                "---\nname: multi-desc\ndescription: |\n"
                "  alpha keyword\n"
                "  beta line\n"
                "---\n# Body\n"
            )
        results = search_local_skills(tmpdir, "alpha")
        assert len(results) == 1
        assert "alpha keyword" in results[0]["description"]
