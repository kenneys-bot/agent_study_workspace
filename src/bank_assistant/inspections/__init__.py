"""质检任务执行与证据校验（M4-03/T18+T19，#19/#20）。

- :mod:`bank_assistant.inspections.schema`：规则快照与模型输出校验（纯逻辑）；
- :mod:`bank_assistant.inspections.evidence`：码点/UTF-16 换算与证据逐字校验；
- :mod:`bank_assistant.inspections.deterministic`：否定/引用他人话语的确定性检查；
- :mod:`bank_assistant.inspections.service`：创建/读取质检运行与规则快照；
- :mod:`bank_assistant.inspections.runner`：worker 侧的一次质检执行与幂等收尾。
"""

from __future__ import annotations

from bank_assistant.inspections.schema import (
    INSPECTION_PROMPT_VERSION,
    InspectionErrorCode,
    InspectionStatus,
    RuleJudgmentPayload,
    RuleSnapshot,
    RuleSnapshotEntry,
)
from bank_assistant.inspections.service import (
    InspectionInputError,
    InspectionNotFound,
    InspectionView,
    TASK_TYPE,
    create_inspection,
    load_inspection,
)

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
    "load_inspection",
]
