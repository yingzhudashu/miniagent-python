"""飞书工具共享辅助函数

消除 feishu_im_tools.py、feishu_doc_tools.py、feishu_bitable_tools.py、feishu_card_tools.py
中的重复配置检查和依赖检查代码。

使用方式：

    from miniagent.assistant.tools.feishu_utils import check_feishu_config, check_lark_oapi

    async def _some_feishu_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        cfg, err = check_feishu_config()
        if err:
            return err
        dep_err = check_lark_oapi()
        if dep_err:
            return dep_err
        # 继续处理...

非工具代码可直接使用 ``require_feishu_config`` / ``require_lark_oapi_installed``，
在缺失配置或依赖时抛出 ``FeishuConfigMissingError`` / ``LarkOapiMissingError``。

重命名说明：从 _feishu_utils.py 重命名为 feishu_utils.py（规范化）。
"""

from __future__ import annotations

from miniagent.agent.types.error_messages import DEPENDENCY_LARK_OAPI_MISSING, FEISHU_CONFIG_MISSING
from miniagent.agent.types.error_prefix import WARNING_PREFIX
from miniagent.agent.types.errors import FeishuConfigMissingError, LarkOapiMissingError
from miniagent.agent.types.tool import ToolResult
from miniagent.assistant.feishu.lark_client import config_from_env
from miniagent.ui.feishu.types import FeishuConfig


def require_feishu_config() -> FeishuConfig:
    """读取飞书配置；缺失必要环境变量时抛出 ``FeishuConfigMissingError``。"""
    cfg = config_from_env()
    if cfg is None:
        raise FeishuConfigMissingError()
    return cfg


def require_lark_oapi_installed() -> None:
    """确认 lark-oapi 已安装；缺失时抛出 ``LarkOapiMissingError``。"""
    try:
        import miniagent.assistant.feishu.lark_client as _lc

        _lc.require_lark_oapi()
    except ImportError as e:
        raise LarkOapiMissingError() from e


def check_feishu_config() -> tuple[FeishuConfig | None, ToolResult | None]:
    """统一检查飞书配置

    检查环境变量 FEISHU_APP_ID 和 FEISHU_APP_SECRET 是否已配置。

    Returns:
        (FeishuConfig, None): 配置成功，返回配置对象
        (None, ToolResult): 配置失败，返回错误 ToolResult
    """
    try:
        return require_feishu_config(), None
    except FeishuConfigMissingError:
        return None, ToolResult(
            success=False,
            content=f"{WARNING_PREFIX} {FEISHU_CONFIG_MISSING}。",
        )


def check_lark_oapi() -> ToolResult | None:
    """统一检查 lark-oapi 依赖

    检查 lark-oapi SDK 是否已安装。

    Returns:
        None: 依赖已安装
        ToolResult: 依赖缺失，返回错误 ToolResult
    """
    try:
        require_lark_oapi_installed()
    except LarkOapiMissingError:
        return ToolResult(
            success=False,
            content=f"{WARNING_PREFIX} {DEPENDENCY_LARK_OAPI_MISSING}。",
        )
    return None


def check_feishu_config_and_lark_oapi() -> tuple[FeishuConfig | None, ToolResult | None]:
    """统一检查飞书配置和 lark-oapi 依赖

    组合检查：先检查配置，再检查依赖。

    Returns:
        (FeishuConfig, None): 检查全部通过
        (None, ToolResult): 任一检查失败
    """
    cfg, cfg_err = check_feishu_config()
    if cfg_err:
        return None, cfg_err
    dep_err = check_lark_oapi()
    if dep_err:
        return None, dep_err
    return cfg, None


__all__ = [
    "require_feishu_config",
    "require_lark_oapi_installed",
    "check_feishu_config",
    "check_lark_oapi",
    "check_feishu_config_and_lark_oapi",
]
