"""错误处理工具模块

提供统一的错误处理装饰器和工具函数，用于包装可能失败的函数，
提供统一的日志记录和错误处理，消除项目中重复的 try-except 模式。

使用示例：
    >>> from miniagent.utils.error_handling import safe_execute
    >>>
    >>> @safe_execute(default_return=[], log_level="warning")
    >>> async def load_history(path: str) -> list:
    >>>     with open(path) as f:
    >>>         return json.load(f)

设计背景见 docs/ENGINEERING.md § 代码质量标准。
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from logging import Logger
from typing import Any, TypeVar

T = TypeVar("T")


def _get_logger(module_name: str) -> Logger:
    """延迟获取 logger，避免循环导入"""
    from miniagent.infrastructure.logger import get_logger
    return get_logger(module_name)


def _log_failure(
    logger: Logger, level: str, message: str, include_trace: bool
) -> None:
    """按指定级别记录失败消息，include_trace 为真时附带异常追踪信息。"""
    if include_trace:
        logger.log(getattr(logger, level, logger.warning), message, exc_info=True)
    else:
        logger.log(getattr(logger, level, logger.warning), message)


def safe_execute(
    default_return: Any = None,
    log_level: str = "warning",
    reraise: bool = False,
    log_exception_trace: bool = False,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """统一的错误处理装饰器

    用于包装可能失败的函数，提供统一的日志记录和错误处理。
    可用于异步和同步函数。

    Args:
        default_return: 失败时的默认返回值
        log_level: 日志级别（debug/info/warning/error/critical）
        reraise: 是否重新抛出异常
        log_exception_trace: 是否记录异常完整追踪信息

    Returns:
        被装饰的函数

    Example:
        >>> @safe_execute(default_return=[], log_level="warning")
        >>> async def load_history(path: str) -> list:
        >>>     with open(path) as f:
        >>>         return json.load(f)

        >>> @safe_execute(reraise=True, log_level="error")
        >>> async def critical_operation() -> None:
        >>>     # 必须成功的操作，失败时抛出异常
        >>>     ...

    Note:
        - 异步函数自动检测并正确处理
        - 日志记录包含函数名和异常信息
        - 使用 lazy logger 获取避免循环导入

    See Also:
        - miniagent.infrastructure.logger: 日志系统
        - docs/ENGINEERING.md: 代码质量标准
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        # 判断是否为异步函数
        import asyncio
        is_async = asyncio.iscoroutinefunction(func)

        if is_async:
            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> T:
                try:
                    return await func(*args, **kwargs)  # type: ignore
                except Exception as e:
                    logger = _get_logger(func.__module__)
                    _log_failure(
                        logger, log_level, f"{func.__name__} 失败: {e}", log_exception_trace
                    )
                    if reraise:
                        raise
                    return default_return  # type: ignore
            return async_wrapper  # type: ignore
        else:
            @wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> T:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    logger = _get_logger(func.__module__)
                    _log_failure(
                        logger, log_level, f"{func.__name__} 失败: {e}", log_exception_trace
                    )
                    if reraise:
                        raise
                    return default_return  # type: ignore
            return sync_wrapper  # type: ignore

    return decorator


def safe_execute_sync(
    default_return: Any = None,
    log_level: str = "warning",
    reraise: bool = False,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """同步函数专用错误处理装饰器

    与 safe_execute 相同，但明确用于同步函数，避免异步检测开销。

    Args:
        default_return: 失败时的默认返回值
        log_level: 日志级别（debug/info/warning/error/critical）
        reraise: 是否重新抛出异常

    Returns:
        被装饰的函数

    Example:
        >>> @safe_execute_sync(default_return=None)
        >>> def parse_config(content: str) -> dict:
        >>>     return json.loads(content)
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger = _get_logger(func.__module__)
                _log_failure(logger, log_level, f"{func.__name__} 失败: {e}", False)
                if reraise:
                    raise
                return default_return  # type: ignore
        return wrapper  # type: ignore

    return decorator


def log_exception(
    func_name: str,
    exception: Exception,
    module_name: str,
    level: str = "warning",
    include_trace: bool = False,
) -> None:
    """独立的异常日志记录函数

    用于手动记录异常，不使用装饰器时可用此函数。

    Args:
        func_name: 函数名
        exception: 异常对象
        module_name: 模块名
        level: 日志级别
        include_trace: 是否包含追踪信息

    Example:
        >>> try:
        >>>     risky_operation()
        >>> except Exception as e:
        >>>     log_exception("risky_operation", e, __name__, level="error")
    """
    logger = _get_logger(module_name)
    _log_failure(logger, level, f"{func_name} 失败: {exception}", include_trace)


__all__ = [
    "safe_execute",
    "safe_execute_sync",
    "log_exception",
]