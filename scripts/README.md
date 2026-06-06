# 维护脚本

本目录脚本不参与 Agent 运行时；在项目根执行 `python scripts/<name>.py`。

| 脚本 | 用途 | 文档 |
|------|------|------|
| `bootstrap_clawhub_skills.py` | 从 ClawHub 安装额外技能 | README、USER_GUIDE §12 |
| `vendor_skill_from_github.py` | GitHub 拉取技能目录（ClawHub 备选） | `clawhub_client` 错误提示 |
| `perf_profile_tracemalloc.py` | 本地 tracemalloc 剖析 | PERFORMANCE.md |
| `compare_perf_snapshots.py` | 对比两次剖析 JSON | PERFORMANCE.md |
| `docstring_inventory.py` | docstring 缺失扫描 | CONTRIBUTING.md |
| `user/` | 用户私有脚本目录（不入库） | user/README.md |

性能验收请使用 `pytest -m perf`，勿再维护独立 verify 脚本。
