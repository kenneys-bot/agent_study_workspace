"""核心异常。

`CapabilityUnavailableError` 表示某个能力（模型、embedding 等）在当前配置下
不可用：SDK 未安装、凭据缺失或端点未配置。调用方必须显式失败，不能静默降级
到随机向量或模拟回复（ADR-0004 / M1-04）。
"""


class CapabilityUnavailableError(RuntimeError):
    """模型能力不可用。message 必须以 "capability unavailable:" 开头。"""
