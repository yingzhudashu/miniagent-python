"""Stable failures raised by the unofficial Xianyu transport."""


class XianyuError(RuntimeError):
    """Base integration failure."""


class XianyuAuthenticationError(XianyuError):
    """The configured cookie can no longer authenticate."""


class XianyuProtocolError(XianyuError):
    """The remote protocol no longer matches the supported contract."""


class XianyuDependencyError(XianyuError):
    """An explicitly required optional runtime dependency is unavailable."""


__all__ = [
    "XianyuAuthenticationError",
    "XianyuDependencyError",
    "XianyuError",
    "XianyuProtocolError",
]
