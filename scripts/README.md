# 维护脚本

- `check_architecture.py`：校验核心分层依赖方向与生产函数 100 行零豁免上限。
- `check_docs.py`：校验 Markdown 本地链接、锚点、索引和易漂移事实。
- `check_wheel_resources.py`：检查构建 Wheel 中的默认配置、技能资源，以及 Python 模块清单是否与当前源码树完全一致，防止旧构建缓存夹带已删除模块。
- `docstring_inventory.py --check`：强制模块、公开接口、复杂顶层私有实现与关键状态机具备有效说明。
- `perf_profile_tracemalloc.py` / `compare_perf_snapshots.py`：生成并比较本地性能快照。

本目录脚本不参与 Agent 运行时；在项目根执行 `python scripts/<name>.py`。

| 脚本 | 用途 | 文档 |
|------|------|------|
| `bootstrap_clawhub_skills.py` | 从 ClawHub 安装额外技能 | README、USER_GUIDE §7 |
| `vendor_skill_from_github.py` | GitHub 拉取技能目录（ClawHub 备选） | `clawhub_client` 错误提示 |
| `perf_profile_tracemalloc.py` | 本地 tracemalloc 剖析 | [PERFORMANCE.md](../docs/PERFORMANCE.md) |
| `compare_perf_snapshots.py` | 对比两次剖析 JSON | [PERFORMANCE.md](../docs/PERFORMANCE.md) |
| `docstring_inventory.py` | docstring 缺失扫描 | [CONTRIBUTING.md](../docs/CONTRIBUTING.md) |
| `user/` | 用户私有脚本目录（不入库） | user/README.md |

性能验收请使用 `pytest -m perf`，勿再维护独立 verify 脚本。
