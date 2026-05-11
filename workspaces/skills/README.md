# 技能目录（`workspaces/skills`）

## 内置基线（开箱）

| 目录 | 说明 |
|------|------|
| **`skill-creator/`** | 自 [anthropics/skills](https://github.com/anthropics/skills) `skills/skill-creator` vendoring（Apache-2.0，见包内 `LICENSE.txt`）。可用 `python scripts/vendor_skill_from_github.py anthropics/skills skills/skill-creator skill-creator` 从上游刷新。 |
| **`skill-vetter/`** | 本仓库维护的**安全审查**说明（与 ClawHub 同名技能公开描述语义对齐；正文独立撰写）。 |

合并许可与同步方式见 [THIRD_PARTY_SKILLS.md](THIRD_PARTY_SKILLS.md)。

覆盖默认路径：设置 **`MINI_AGENT_SKILLS`** 指向其它目录时，引擎只扫描该目录（不会自动合并本路径）。

## 从 ClawHub 安装更多（可选）

```bash
python scripts/bootstrap_clawhub_skills.py
```

- **嵌套 slug**：ClawHub 上可能是 `author/skill-name` 形式。`download()` 会把文件写入 **`skills_root` 下以 slug 最后一段命名的目录**（例如 `.../skills/skill-name/`），与 [`discover_skill_packages`](../../miniagent/skills/loader.py) 只认一级子目录的规则一致。若你曾用手工方式把包装在 `author/skill-name/` 嵌套路径下，请**扁平化**到单层目录，否则引擎不会加载。
- **详情无 files**：`GET /skills/{slug}` **可能不返回 `files`**；客户端会再尝试 `GET .../download`（若站点返回 JSON 文件列表）。仍失败时请用 **`scripts/vendor_skill_from_github.py`** 从公开 Git 同步，或手动拷贝技能目录。

## 包结构约定

见源码 [`miniagent/skills/loader.py`](../../miniagent/skills/loader.py) 顶部注释：每个一级子目录为一个技能包，需包含包级 `SKILL.md`；带 Python 工具时放在 `skills/<id>/SKILL.md` 与 `tools.py`。
