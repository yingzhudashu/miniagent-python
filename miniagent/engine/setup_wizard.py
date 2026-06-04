"""首次使用配置引导。

检测用户是否首次运行（无 config.user.json），
并提供交互式配置引导。

流程：
1. 检测首次运行
2. 提示是否运行引导
3. 询问 API 密钥、模型、端点等
4. 保存到 config.user.json
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def detect_first_time_setup() -> bool:
    """检测是否首次运行（无 config.user.json）。

    Returns:
        True 如果需要首次配置
    """
    project_root = Path(__file__).parent.parent.parent
    config_path = project_root / "config.user.json"
    return not config_path.exists()


def run_setup_wizard() -> dict[str, Any]:
    """运行交互式配置引导。

    Returns:
        配置字典（用于保存到 config.user.json）

    Note:
        此函数会与用户交互（input()），仅在 CLI 模式下调用
    """
    print("\n🚀 MiniAgent 首次配置")
    print("=" * 50)
    print("欢迎使用 MiniAgent！这是您的首次运行。")
    print("让我们设置一些基本配置...\n")

    config: dict[str, Any] = {}

    # 1. 检查 API 密钥
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("⚠️  OPENAI_API_KEY 未设置")
        print("\n请选择配置方式：")
        print("  1. 现在输入 API 密钥")
        print("  2. 稍后手动配置")
        print("  3. 跳过（使用默认配置）")

        choice = input("\n请选择 [1/2/3]: ").strip()

        if choice == "1":
            key = input("请输入 OpenAI API 密钥: ").strip()
            if key:
                config["secrets"] = {"openai_api_key": key}
                print("✅ API 密钥已保存")
        elif choice == "2":
            print("\n请稍后手动创建 config.user.json：")
            print("  {")
            print("    \"secrets\": {")
            print("      \"openai_api_key\": \"your-key-here\"")
            print("    }")
            print("  }")
        else:
            print("⚠️  未配置 API 密钥，LLM 功能将无法使用")

    # 2. 模型选择
    print("\n📝 模型配置")
    print("默认模型: gpt-4o-mini")
    print("常用模型: gpt-4o, gpt-4o-mini, gpt-3.5-turbo")

    model = input("模型名称 (或按 Enter 使用默认): ").strip()
    if model:
        if "model" not in config:
            config["model"] = {}
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
        if "model" not in config:
            config["model"] = {}
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
    print("    \"secrets\": {")
    print("      \"feishu_app_id\": \"your-app-id\",")
    print("      \"feishu_app_secret\": \"your-app-secret\"")
    print("    }")
    print("  }")

    # 完成
    print("\n" + "=" * 50)
    print("🎉 配置完成！")

    return config


def save_setup_config(config: dict[str, Any]) -> None:
    """保存配置到 config.user.json。

    Args:
        config: 配置字典

    Note:
        如果文件已存在，会合并配置（保留现有内容）
    """
    project_root = Path(__file__).parent.parent.parent
    config_path = project_root / "config.user.json"

    # 加载现有配置（如果存在）
    existing: dict[str, Any] = {}
    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}

    # 合并配置
    merged: dict[str, Any] = {**existing}

    for key, value in config.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            # 合并字典
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value

    # 写入文件
    config_path.write_text(
        json.dumps(merged, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"\n✅ 配置已保存到: {config_path}")
    print("   您可以随时编辑此文件来调整设置")


def run_interactive_setup() -> bool:
    """运行首次配置引导（如果需要）。

    Returns:
        True 如果运行了引导，False 否则

    Note:
        - 仅在首次运行时触发
        - 用户可以选择跳过
        - 非阻塞式，不影响正常启动流程
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
        print("\n跳过配置。您可以稍后手动创建 config.user.json")
        print("参考 config.defaults.json 了解可用配置项")
        return False

    # 运行引导
    config = run_setup_wizard()

    if config:
        save_setup_config(config)
        print("\n💡 提示：配置文件修改后无需重启")
        print("   设置 features.config_hot_reload=true 可自动加载更改")

    return True


__all__ = [
    "detect_first_time_setup",
    "run_setup_wizard",
    "save_setup_config",
    "run_interactive_setup",
]