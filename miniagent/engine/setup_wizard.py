"""首次使用配置引导。

检测用户是否首次运行（无 config.user.json），
并提供交互式配置引导。

流程：
1. 检测首次运行
2. 提示是否运行引导
3. 询问 API 密钥、模型、端点等
4. 保存到 config.user.json 并热加载到内存/环境变量

须在 ``compat.unified_entry`` 加载凭据与 LLM 客户端之前调用。
"""

from __future__ import annotations

import json
import sys
from typing import Any

from miniagent.infrastructure.json_config import get_user_config_path, reload_runtime_config


def _copy_defaults_hint() -> str:
    """返回当前平台适用的 config 模板复制命令。"""
    if sys.platform == "win32":
        return "copy config.defaults.json config.user.json"
    return "cp config.defaults.json config.user.json"


def detect_first_time_setup() -> bool:
    """检测是否首次运行（无 config.user.json）。

    Returns:
        True 如果需要首次配置
    """
    return not get_user_config_path().exists()


def run_setup_wizard() -> dict[str, Any]:
    """运行交互式配置引导。

    Returns:
        配置字典（用于保存到 config.user.json）。结构示例::

            {
                "secrets": {"openai_api_key": "sk-..."},
                "model": {"model": "gpt-4o", "base_url": "https://..."},
                "paths": {"state_dir": "workspaces"},
            }

    Note:
        此函数会与用户交互（``input()``），仅在 CLI 模式下调用。
    """
    print("\n🚀 MiniAgent 首次配置")
    print("=" * 50)
    print("欢迎使用 MiniAgent！这是您的首次运行。")
    print("让我们设置一些基本配置...\n")

    config: dict[str, Any] = {}

    # 1. API 密钥（写入 config.user.json secrets）
    print("🔑 API 凭据")
    print("\n请选择配置方式：")
    print("  1. 现在输入 API 密钥")
    print("  2. 稍后手动配置")
    print("  3. 跳过（使用默认配置）")

    choice = input("\n请选择 [1/2/3]: ").strip()

    if choice == "1":
        key = input("请输入 OpenAI API 密钥: ").strip()
        if key:
            config["secrets"] = {"openai_api_key": key}
            print("✅ API 密钥已记录，将在保存后加载")
        else:
            print("⚠️  未输入 API 密钥，LLM 功能将无法使用")
    elif choice == "2":
        copy_cmd = _copy_defaults_hint()
        print("\n请稍后手动创建 config.user.json：")
        print(f"  {copy_cmd}")
        print("  然后在 secrets 中填写 openai_api_key")
    else:
        print("⚠️  未配置 API 密钥，LLM 功能将无法使用")

    # 2. 模型选择
    print("\n📝 模型配置")
    print("默认模型: gpt-4o-mini")
    print("常用模型: gpt-4o, gpt-4o-mini, gpt-3.5-turbo")

    model = input("模型名称 (或按 Enter 使用默认): ").strip()
    if model:
        config.setdefault("model", {})
        config["model"]["model"] = model
        print(f"✅ 模型设置为: {model}")

    # 3. API 端点（用于第三方服务）
    print("\n🌐 API 端点")
    print("默认端点: https://api.openai.com/v1")
    print("常用端点:")
    print("  - Azure: https://your-resource.openai.azure.com/openai/deployments/your-deployment")
    print("  - 国内代理: https://api.example.com/v1")

    base_url = input("自定义端点 (或按 Enter 使用默认): ").strip()
    if base_url:
        config.setdefault("model", {})
        config["model"]["base_url"] = base_url
        print(f"✅ API 端点设置为: {base_url}")

    # 4. 工作目录
    print("\n📁 工作目录")
    print("默认目录: workspaces")

    state_dir = input("自定义工作目录 (或按 Enter 使用默认): ").strip()
    if state_dir:
        config["paths"] = {"state_dir": state_dir}
        print(f"✅ 工作目录设置为: {state_dir}")

    # 5. 飞书配置（可选）
    print("\n📱 飞书集成（可选）")
    print("如需飞书集成，请稍后在 config.user.json 配置:")
    print("  {")
    print('    "secrets": {')
    print('      "feishu_app_id": "your-app-id",')
    print('      "feishu_app_secret": "your-app-secret"')
    print("    }")
    print("  }")

    # 完成
    print("\n" + "=" * 50)
    print("🎉 配置完成！")

    return config


def _apply_saved_config() -> None:
    """将磁盘上的 config.user.json 同步到内存配置与环境变量。"""
    reload_runtime_config()


def save_setup_config(config: dict[str, Any]) -> None:
    """保存配置到 config.user.json。

    Args:
        config: 配置字典

    Note:
        如果文件已存在，会合并配置（保留现有内容）。
        写入后会调用 ``reload_config()`` 与 ``load_secrets_from_project_root()``，
        并丢弃已缓存的 AsyncOpenAI 客户端以便后续按新配置重建。
    """
    config_path = get_user_config_path()

    # 加载现有配置（如果存在）
    existing: dict[str, Any] = {}
    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}

    # 合并配置
    merged: dict[str, Any] = {**existing}

    for key, value in config.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value

    config_path.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    _apply_saved_config()

    print(f"\n✅ 配置已保存到: {config_path}")
    print("   您可以随时编辑此文件来调整设置")


def run_interactive_setup() -> bool:
    """运行首次配置引导（如果需要）。

    Returns:
        True 如果用户确认并完成了引导流程（含跳过所有可选项），
        False 如果无需引导或用户在入口处选择跳过。

    Note:
        - 仅在首次运行时触发（无 config.user.json）
        - 用户可以在入口处选择跳过整个引导
        - 须在 ``load_secrets_from_project_root()`` 与 ``get_shared_async_openai()``
          之前调用，以便向导中填写的 API 密钥在本进程内生效
    """
    if not detect_first_time_setup():
        return False

    print("\n" + "=" * 50)
    print("🎉 欢迎使用 MiniAgent！")
    print("=" * 50)
    print("\n这是您的首次运行。")
    print("是否运行配置引导？")

    response = input("\n运行配置引导? [Y/n]: ").strip().lower()

    if response in ("n", "no", "否"):
        copy_cmd = _copy_defaults_hint()
        print("\n跳过配置。您可以稍后手动创建 config.user.json")
        print(f"  {copy_cmd}")
        print("参考 config.defaults.json 了解可用配置项")
        return False

    config = run_setup_wizard()

    if config:
        save_setup_config(config)
        print("\n💡 提示：配置已写入并在本进程继续启动时生效。")
        print("   运行中修改 config.user.json 可使用 /reload-config，")
        print("   或设置 features.config_hot_reload=true 自动监听变更。")

    return True


__all__ = [
    "detect_first_time_setup",
    "run_setup_wizard",
    "save_setup_config",
    "run_interactive_setup",
]
