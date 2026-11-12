"""评测领域错误：预算中止与数据/配置不满足前置条件（T27，#28）。"""

from __future__ import annotations

__all__ = ["BudgetExceeded", "EvalError", "EvalBlocked"]


class EvalError(RuntimeError):
    """评测前置条件不满足（数据集哈希、快照漂移、配置冲突等）。"""


class EvalBlocked(EvalError):
    """能力不可用（凭据/网络/embedding），必须如实标记 BLOCKED。"""


class BudgetExceeded(RuntimeError):
    """live 调用达到配置的预算上限；停止后续调用并保留已完成结果。"""
