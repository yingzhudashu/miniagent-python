# 第三方技能（`workspaces/skills`）

## skill-creator

- **来源**：[anthropics/skills](https://github.com/anthropics/skills) 仓库 `main` 分支，路径 `skills/skill-creator/`。
- **许可证**：包内 [`skill-creator/LICENSE.txt`](skill-creator/LICENSE.txt)（Apache License 2.0）。
- **溯源记录**（便于复现与审计）：
  - **记录日期**：2026-05-10
  - **上游路径快照 commit**（`skills/skill-creator` 最近一条）：`b9e19e6f44773509fbdd7001d77ff41a49a486c1`（见 [GitHub commits API 按 path 过滤](https://api.github.com/repos/anthropics/skills/commits?path=skills/skill-creator&per_page=1)；刷新 vendoring 后请更新此 SHA）。
- **同步方式**：维护者可运行  
  `python scripts/vendor_skill_from_github.py anthropics/skills skills/skill-creator skill-creator`  
  从上游刷新（会先清空本地 `workspaces/skills/skill-creator` 再写入）。脚本使用未认证 GitHub API，频繁运行可能触发 rate limit；可设置环境变量 **`GITHUB_TOKEN`**（只读 fine-grained 或 classic PAT）以提高配额。

## skill-vetter

- **来源**：本仓库维护的 **Mini Agent 配套说明**，与 ClawHub 上同名技能的公开描述（安全审查清单、风险分级）在语义上对齐；正文为独立撰写，**非** ClawHub 二进制包或闭源内容的逐字拷贝。
- **许可证**：与主仓库一致（MIT），可按需在包内补充更细的免责声明。

## ClawHub API 说明

`miniagent.skills.clawhub_client.download()` 优先从 `GET /api/v1/skills/{slug}`（及 `latestVersion.files`）取内联 `files`；若无，则再尝试 `GET .../skills/{slug}/download`（若站点返回 JSON 文件列表）。仍无 `files` 时无法自动落盘，请使用本目录已 vendoring 的包，或 `scripts/vendor_skill_from_github.py` 从 **GitHub 等公开 git 源** 同步。

含 `author/pkg` 的 slug 会安装到 **`skills_root` 下名为 `pkg` 的一级目录**（与本地发现规则一致）。
