"""M6 数据集包：合成数据构建、冻结、拆分守卫（T26，#27）。

只提供离线能力，不连接数据库、不调用模型；入口见
``scripts/build_dataset.py`` 与 :mod:`bank_assistant.datasets.split_guard`。

公开名字惰性导出，避免 ``python -m bank_assistant.datasets.split_guard`` 在
包初始化时先导入构建链而产生 runpy 警告。
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "DATASET_ID",
    "DEFAULT_SEED",
    "DatasetBuild",
    "DatasetBuildError",
    "FrozenSplitError",
    "SplitLeakageError",
    "SplitPurpose",
    "build_dataset",
    "require_split_for",
    "verify_dataset",
    "write_committed_sample",
    "write_dataset",
]

_LAZY_EXPORTS: dict[str, str] = {
    "DATASET_ID": "bank_assistant.datasets.build",
    "DEFAULT_SEED": "bank_assistant.datasets.build",
    "DatasetBuild": "bank_assistant.datasets.build",
    "DatasetBuildError": "bank_assistant.datasets.build",
    "FrozenSplitError": "bank_assistant.datasets.build",
    "build_dataset": "bank_assistant.datasets.build",
    "verify_dataset": "bank_assistant.datasets.build",
    "write_committed_sample": "bank_assistant.datasets.build",
    "write_dataset": "bank_assistant.datasets.build",
    "SplitLeakageError": "bank_assistant.datasets.split_guard",
    "SplitPurpose": "bank_assistant.datasets.split_guard",
    "require_split_for": "bank_assistant.datasets.split_guard",
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
