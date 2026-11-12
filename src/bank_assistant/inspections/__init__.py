"""质检任务执行与证据校验（M4-03/T18+T19，#19/#20）。

- :mod:`bank_assistant.inspections.schema`：规则快照与模型输出校验（纯逻辑）；
- :mod:`bank_assistant.inspections.evidence`：码点/UTF-16 换算与证据逐字校验；
- :mod:`bank_assistant.inspections.deterministic`：否定/引用他人话语的确定性检查；
- :mod:`bank_assistant.inspections.service`：创建/读取质检运行与规则快照；
- :mod:`bank_assistant.inspections.runner`：worker 侧的一次质检执行与幂等收尾。

公开名字改成**惰性导出**：纯子模块（如 ``deterministic``）可以被离线工具
（M6 数据集构建）导入，而不触发 ``service``/数据库配置链；直接
``from bank_assistant.inspections import RuleSnapshot`` 的行为保持不变。
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "INSPECTION_PROMPT_VERSION",
    "InspectionErrorCode",
    "InspectionInputError",
    "InspectionNotFound",
    "InspectionStatus",
    "InspectionView",
    "RuleJudgmentPayload",
    "RuleSnapshot",
    "RuleSnapshotEntry",
    "TASK_TYPE",
    "create_inspection",
    "effective_machine_verdict",
    "finding_evidence_valid",
    "load_inspection",
]

_LAZY_EXPORTS: dict[str, str] = {
    "INSPECTION_PROMPT_VERSION": "bank_assistant.inspections.schema",
    "InspectionErrorCode": "bank_assistant.inspections.schema",
    "InspectionStatus": "bank_assistant.inspections.schema",
    "RuleJudgmentPayload": "bank_assistant.inspections.schema",
    "RuleSnapshot": "bank_assistant.inspections.schema",
    "RuleSnapshotEntry": "bank_assistant.inspections.schema",
    "InspectionInputError": "bank_assistant.inspections.service",
    "InspectionNotFound": "bank_assistant.inspections.service",
    "InspectionView": "bank_assistant.inspections.service",
    "TASK_TYPE": "bank_assistant.inspections.service",
    "create_inspection": "bank_assistant.inspections.service",
    "effective_machine_verdict": "bank_assistant.inspections.service",
    "finding_evidence_valid": "bank_assistant.inspections.service",
    "load_inspection": "bank_assistant.inspections.service",
}


def __getattr__(name: str) -> Any:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
