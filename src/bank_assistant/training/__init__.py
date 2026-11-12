"""条件性意图分类微调（T28，#29）。

只对外暴露两类 seam：

- :func:`bank_assistant.training.config.load_train_config`：严格配置校验；
- :func:`bank_assistant.training.trainer.run_experiment`：dry-run / 真实训练。

torch/transformers/peft 是可选依赖，导入本包不会加载它们。
"""

from __future__ import annotations

from bank_assistant.training.config import (
    TrainConfig,
    TrainConfigError,
    load_train_config,
)
from bank_assistant.training.dataset import (
    IntentDataError,
    IntentDataset,
    IntentExample,
    load_intent_split,
)
from bank_assistant.training.evaluate import (
    IntentMetrics,
    IntentPrediction,
    compare_metrics,
    compute_intent_metrics,
)
from bank_assistant.training.manifest import (
    STATUSES,
    TrainingManifestError,
    validate_training_manifest,
)

__all__ = [
    "IntentDataError",
    "IntentDataset",
    "IntentExample",
    "IntentMetrics",
    "IntentPrediction",
    "STATUSES",
    "TrainConfig",
    "TrainConfigError",
    "TrainingManifestError",
    "compare_metrics",
    "compute_intent_metrics",
    "load_intent_split",
    "load_train_config",
    "validate_training_manifest",
]
