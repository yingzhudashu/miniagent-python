"""验证 ``tests/evaluation`` 目录可被 pytest 收集（marker 由 ``conftest`` 统一附加）。

不含网络或密钥依赖；默认主 CI 使用 ``-m 'not evaluation'`` 跳过本文件，
可选 workflow 仍可跑 ``-m evaluation`` 以确认该子树未被破坏。
"""


def test_evaluation_subpackage_collectible() -> None:
    assert True
