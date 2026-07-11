# 第三方技能许可与合规说明

> 本文件为 **SSOT**：记录 `workspaces/skills/` 下内置与已安装第三方技能包的来源、许可证与合规要点。  
> 用户指南见 [docs/USER_GUIDE.md](../../docs/USER_GUIDE.md) §13；ClawHub 安装脚本见 `scripts/bootstrap_clawhub_skills.py`。

---

## 内置基线技能

| 目录 | 来源 | 许可证 | 说明 |
|------|------|--------|------|
| `skill-creator` | [anthropics/skills](https://github.com/anthropics/skills) | Apache-2.0（见包内 `LICENSE.txt`） | 仓库模板位于 `miniagent/skills/templates/skill-creator/`；editable 安装或克隆后复制到本目录 |
| `skill-vetter` | 本仓库配套 | 与项目 MIT 一致 | 模板位于 `miniagent/skills/templates/skill-vetter/`；可通过 `miniagent install-skill skill-vetter` 安装 |

**wheel 安装说明**：仅从 PyPI 安装时，`workspaces/skills/` 下可能没有预置文件。需要基线时请克隆仓库、`pip install -e .`，或手动复制上述模板目录。

---

## 从 ClawHub 安装的技能

通过 `scripts/bootstrap_clawhub_skills.py` 或 Agent 的 `search_skills` / 市场 API 安装的包，**不由本仓库分发**。安装前请：

1. 在 [ClawHub](https://clawhub.ai) 核对技能页上的 **slug**、作者与许可说明。
2. 安装目录为 slug **最后一段**（与 `skill_install_dir_name` 一致），便于引擎扫描一级子目录。
3. 安装后在本表 **追加一行**，记录 slug、安装日期、许可证（若页面未标明则标注「未知，需自行审查」）。

| slug | 安装目录 | 许可证 | 安装日期 | 备注 |
|------|----------|--------|----------|------|
| _(示例)_ `author/my-skill` | `my-skill` | MIT | YYYY-MM-DD | 请替换为实际记录 |

---

## 合规与使用建议

- **审查优先**：加载前可用内置 `skill-vetter` 技能或人工阅读 `SKILL.md`、脚本与网络请求，确认无越权文件访问、凭据外泄或恶意依赖。
- **许可证义务**：遵守各包 LICENSE；Apache-2.0 / MIT 等常见许可需保留版权声明与 NOTICE（若存在）。
- **网络与密钥**：ClawHub 下载与技能运行时可能访问外网；勿在不可信技能中硬编码或传入生产密钥。
- **卸载**：删除 `workspaces/skills/<目录名>/` 并从上表移除对应行即可；重启 Agent 后不再加载。

---

## 相关文档

- [docs/USER_GUIDE.md](../../docs/USER_GUIDE.md) §13 — 技能与 ClawHub 用户说明
- [docs/SECURITY.md](../../docs/SECURITY.md) — 沙箱与工具权限
- `miniagent/skills/clawhub_client.py` — 市场 API 与本地降级行为
