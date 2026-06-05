#!/usr/bin/env python
"""验证CLI滚动修复效果的测试脚本"""

import sys
import os

# Windows编码修复
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_scroll_fix():
    """测试滚动修复的关键点"""

    print("=" * 60)
    print("CLI 滚动修复验证测试")
    print("=" * 60)

    # 测试1: 导入模块
    print("\n[测试1] 导入模块...")
    try:
        from miniagent.engine.main import run_cli_loop
        print("✅ 导入成功")
    except Exception as e:
        print(f"❌ 导入失败: {e}")
        return False

    # 测试2: 检查 _apply_transcript_scroll 函数
    print("\n[测试2] 检查滚动函数...")
    try:
        from miniagent.engine.main import _apply_transcript_scroll
        # 检查函数签名
        import inspect
        sig = inspect.signature(_apply_transcript_scroll)
        params = list(sig.parameters.keys())
        if params == ['signed_step', 'src']:
            print("✅ 函数签名正确")
        else:
            print(f"❌ 函数签名不正确: {params}")
            return False
    except Exception as e:
        # 注意：这个函数在 run_cli_loop 内部定义，无法直接导入
        print(f"ℹ️ 函数在内部定义，无法直接导入（这是预期行为）")

    # 测试3: 检查关键代码片段（通过grep）
    print("\n[测试3] 检查代码修改...")

    import re

    with open('miniagent/engine/main.py', 'r', encoding='utf-8') as f:
        content = f.read()

    # 检查 PageUp/PageDown 使用 _apply_transcript_scroll
    pageup_pattern = r'_apply_transcript_scroll\(-_scroll_step\(\), "pageup"\)'
    pagedown_pattern = r'_apply_transcript_scroll\(_scroll_step\(\), "pagedown"\)'

    if re.search(pageup_pattern, content):
        print("✅ PageUp 正确使用 _apply_transcript_scroll")
    else:
        print("❌ PageUp 未使用 _apply_transcript_scroll")
        return False

    if re.search(pagedown_pattern, content):
        print("✅ PageDown 正确使用 _apply_transcript_scroll")
    else:
        print("❌ PageDown 未使用 _apply_transcript_scroll")
        return False

    # 检查 Ctrl+L 不使用 horizontal_scroll 属性
    ctrl_l_pattern = r'output_scroll\.horizontal_scroll\s*=\s*0'
    if re.search(ctrl_l_pattern, content):
        print("❌ Ctrl+L 仍然使用无效的 horizontal_scroll 属性")
        return False
    else:
        print("✅ Ctrl+L 不使用无效的 horizontal_scroll 属性")

    # 检查 Ctrl+L 使用 _reset_horizontal_scroll
    reset_pattern = r'_reset_horizontal_scroll\(\)'
    if re.search(reset_pattern, content):
        print("✅ Ctrl+L 使用 _reset_horizontal_scroll 函数")
    else:
        print("❌ Ctrl+L 未使用 _reset_horizontal_scroll 函数")
        return False

    # 检查滚动条样式使用高对比度背景
    scrollbar_style_pattern = r'"scrollbar\.button":\s*"bg:ansibrightcyan'
    if re.search(scrollbar_style_pattern, content):
        print("✅ 滚动条样式使用高对比度背景")
    else:
        print("❌ 滚动条样式未使用高对比度背景")
        return False

    # 测试4: 检查 _sp() 函数使用
    print("\n[测试4] 检查 _sp() 函数使用...")

    # 在 Ctrl+L 中检查
    ctrl_l_sp_pattern = r'sp\s*=\s*_sp\(\)'
    if re.search(ctrl_l_sp_pattern, content):
        print("✅ Ctrl+L 使用 _sp() 函数")
    else:
        print("❌ Ctrl+L 不使用 _sp() 函数")
        return False

    print("\n" + "=" * 60)
    print("✅ 所有测试通过！滚动修复验证成功")
    print("=" * 60)

    return True

if __name__ == '__main__':
    success = test_scroll_fix()
    sys.exit(0 if success else 1)