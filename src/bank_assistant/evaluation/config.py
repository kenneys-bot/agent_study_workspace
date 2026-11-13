"""EVAL_CONFIG 加载与校验（T27，#28）。

配置只描述 **怎么评测**：模式、数据版本、样本上限、预算与价目表。凭据永远
不在配置里，只从环境/.env 读取；价目表必须写明来源与生效日期，报告里的成本
因此可以被核查，而不是编造数字。

支持 JSON 与 YAML。相对路径按 **进程当前目录** 解析（`make eval` 在仓库根
目录执行），不按配置文件所在目录解析，避免 Ubuntu 与本机路径不一致。

两种模式严格分离：

- ``mode: replay``：确定性回放，禁用真实模型与真实 embedding，无网络、无成本；
- ``mode: live``：真实 DeepSeek 调用，必须给 ``max_items``（样本上限）与
  ``budget_cny``（预算上限），并配置匹配的价目表。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from bank_assistant.datasets.corpus import CORPUS_AS_OF
from bank_assistant.datasets.split_guard import require_frozen_test_split

__all__ = [
    "BUSINESS_FILTER_MODES",
    "EMBEDDING_TEXT_MODES",
    "EvalConfig",
    "EvalConfigError",
    "MODES",
    "PriceEntry",
    "RetrievalOptions",
    "ScenarioRetrievalOverride",
    "TASKS",
    "load_eval_config",
    "load_eval_payload",
    "load_retrieval_options",
    "parse_retrieval_options",
]

MODES: frozenset[str] = frozenset({"replay", "live"})
TASKS: tuple[str, ...] = ("retrieval", "inspection")
EMBEDDING_PROVIDERS: frozenset[str] = frozenset({"replay_lexical", "fastembed"})
# T36：检索改进必须可单独回退，因此全部走 EVAL_CONFIG 的 retrieval 段。
EMBEDDING_TEXT_MODES: frozenset[str] = frozenset(
    {"chunk", "title_chunk", "title_source_chunk"}
)
BUSINESS_FILTER_MODES: frozenset[str] = frozenset({"hard", "soft", "off"})

_TOP_LEVEL_KEYS: frozenset[str] = frozenset(
    {
        "mode",
        "split",
        "dataset_id",
        "dataset_version",
        "dataset_dir",
        "artifact_root",
        "seed",
        "top_k",
        "min_similarity",
        "max_items",
        "budget_cny",
        "model",
        "embedding",
        "pricing",
        "scope_id",
        "knowledge_as_of",
        "tasks",
        "run_id",
        "notes",
        "retrieval",
    }
)
_MODEL_KEYS: frozenset[str] = frozenset({"provider", "model"})
_EMBEDDING_KEYS: frozenset[str] = frozenset({"provider"})
_PRICE_KEYS: frozenset[str] = frozenset(
    {
        "provider",
        "model",
        "input_cny_per_mtok",
        "output_cny_per_mtok",
        "source",
        "as_of",
        "note",
    }
)


class EvalConfigError(ValueError):
    """EVAL_CONFIG 不合法；必须显式失败，不能静默补默认值继续跑。"""


@dataclass(frozen=True, slots=True)
class PriceEntry:
    """一条供应商价目；``source`` 与 ``as_of`` 使成本可核查。"""

    provider: str
    model: str
    input_cny_per_mtok: float
    output_cny_per_mtok: float
    source: str
    as_of: str
    note: str | None = None

    def __post_init__(self) -> None:
        if not self.provider or not self.model:
            raise EvalConfigError("价目条目必须写明 provider 与 model")
        if self.input_cny_per_mtok < 0 or self.output_cny_per_mtok < 0:
            raise EvalConfigError("价目不能为负")
        if not self.source.strip() or not self.as_of.strip():
            raise EvalConfigError(
                "价目条目必须写明 source 与 as_of；没有来源的价格视为编造"
            )


@dataclass(frozen=True, slots=True)
class ScenarioRetrievalOverride:
    """单个场景的检索参数覆盖；``None`` 表示沿用全局值。

    T36 允许按场景调参（例如 complaint 放宽业务过滤），但每个覆盖都显式
    写在配置里，能单独删掉回退，不做隐式特判。
    """

    business_filter: str | None = None
    min_similarity: float | None = None
    top_k: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "business_filter": self.business_filter,
            "min_similarity": self.min_similarity,
            "top_k": self.top_k,
        }


@dataclass(frozen=True, slots=True)
class RetrievalOptions:
    """检索改进开关；默认值等价于 T27 的旧行为（硬过滤 + 仅分块正文）。

    - ``embedding_text``：``chunk`` 只用分块正文；``title_chunk`` 额外拼标题；
      ``title_source_chunk`` 再拼来源类型。改变它就会改变索引向量，
      ``index_version`` 随之变化（ADR-0005）。
    - ``business_filter``：``hard`` 只保留业务内候选；``soft`` 给业务内候选
      加 ``business_bonus`` 相似度奖励但不硬排除跨业务候选；``off`` 不过滤。
    """

    embedding_text: str = "chunk"
    business_filter: str = "hard"
    business_bonus: float = 0.02
    rerank_alpha: float = 0.0
    rerank_pool: int = 20
    scenario_overrides: Mapping[str, ScenarioRetrievalOverride] = field(
        default_factory=dict
    )

    def for_scenario(self, scenario: str | None) -> ScenarioRetrievalOverride:
        if scenario is None:
            return ScenarioRetrievalOverride()
        return self.scenario_overrides.get(scenario, ScenarioRetrievalOverride())

    def to_dict(self) -> dict[str, Any]:
        return {
            "embedding_text": self.embedding_text,
            "business_filter": self.business_filter,
            "business_bonus": self.business_bonus,
            "rerank_alpha": self.rerank_alpha,
            "rerank_pool": self.rerank_pool,
            "scenario_overrides": {
                scenario: override.to_dict()
                for scenario, override in self.scenario_overrides.items()
            },
        }


@dataclass(frozen=True, slots=True)
class EvalConfig:
    """一次评测运行的完整配置；构造后不再变化。"""

    mode: str
    split: str
    dataset_id: str
    dataset_version: str
    dataset_dir: Path
    artifact_root: Path
    seed: int
    top_k: int
    min_similarity: float
    max_items: int | None
    budget_cny: float
    model_provider: str
    model_name: str
    embedding_provider: str
    pricing: tuple[PriceEntry, ...]
    scope_id: str
    knowledge_as_of: datetime
    tasks: tuple[str, ...]
    run_id: str | None = None
    notes: Mapping[str, Any] | None = None
    retrieval: RetrievalOptions = field(default_factory=RetrievalOptions)

    @property
    def model_mode(self) -> str:
        """契约 §6 的模型来源；live 走云模型，replay 只是流程回放。"""
        return "replay" if self.mode == "replay" else "cloud"

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    def price_for(self, provider: str, model: str) -> PriceEntry:
        for entry in self.pricing:
            if entry.provider == provider and entry.model == model:
                return entry
        raise EvalConfigError(
            f"价目表中没有 {provider}/{model}；成本不能凭空估算，"
            "请在 EVAL_CONFIG.pricing 中补充来源与生效日期"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "split": self.split,
            "dataset_id": self.dataset_id,
            "dataset_version": self.dataset_version,
            "dataset_dir": self.dataset_dir.as_posix(),
            "artifact_root": self.artifact_root.as_posix(),
            "seed": self.seed,
            "top_k": self.top_k,
            "min_similarity": self.min_similarity,
            "max_items": self.max_items,
            "budget_cny": self.budget_cny,
            "model": {"provider": self.model_provider, "model": self.model_name},
            "embedding": {"provider": self.embedding_provider},
            "scope_id": self.scope_id,
            "knowledge_as_of": self.knowledge_as_of.isoformat(),
            "tasks": list(self.tasks),
            "run_id": self.run_id,
            "retrieval": self.retrieval.to_dict(),
        }


def load_eval_payload(path: Path | str) -> dict[str, Any]:
    """读取原始 EVAL_CONFIG（YAML/JSON）但不做 split 守卫，供 dev 调参复用。"""
    return _read_payload(Path(path))


def _read_payload(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise EvalConfigError(f"EVAL_CONFIG 不存在：{path}")
    suffix = path.suffix.lower()
    raw = path.read_text("utf-8")
    if suffix == ".json":
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EvalConfigError(f"EVAL_CONFIG JSON 解析失败：{exc}") from exc
    elif suffix in {".yaml", ".yml"}:
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - pyyaml 随 chromadb 安装
            raise EvalConfigError(
                "读取 YAML 配置需要 PyYAML；请改用 JSON 或安装 PyYAML"
            ) from exc
        payload = yaml.safe_load(raw)
    else:
        raise EvalConfigError(
            f"不支持的配置文件扩展名 {suffix!r}；只接受 .json/.yaml/.yml"
        )
    if not isinstance(payload, dict):
        raise EvalConfigError("EVAL_CONFIG 顶层必须是对象")
    return payload


def _require(section: Mapping[str, Any], keys: tuple[str, ...], label: str) -> None:
    missing = [key for key in keys if key not in section]
    if missing:
        raise EvalConfigError(f"{label} 缺少字段：{', '.join(missing)}")


def _check_unknown(section: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    unknown = sorted(set(section) - allowed)
    if unknown:
        raise EvalConfigError(f"{label} 含未知字段：{', '.join(unknown)}")


def _positive_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise EvalConfigError(f"{label} 必须是正整数")
    return value


def _number(value: Any, label: str, *, minimum: float | None = None) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise EvalConfigError(f"{label} 必须是数字")
    number = float(value)
    if minimum is not None and number < minimum:
        raise EvalConfigError(f"{label} 不能小于 {minimum}")
    return number


def parse_retrieval_options(payload: Any) -> RetrievalOptions:
    """解析 ``retrieval`` 段；缺省返回旧行为，显式写错就失败。

    单独暴露入口，是为了让只跑 dev 的调参脚本复用同一份配置而不必通过
    ``load_eval_config`` 的冻结 test 守卫（正式评测仍然只允许 test）。
    """
    if payload is None:
        return RetrievalOptions()
    if not isinstance(payload, Mapping):
        raise EvalConfigError("retrieval 段必须是对象")
    _check_unknown(
        payload,
        frozenset(
            {
                "embedding_text",
                "business_filter",
                "business_bonus",
                "rerank_alpha",
                "rerank_pool",
                "scenario_overrides",
            }
        ),
        "retrieval",
    )
    embedding_text = str(payload.get("embedding_text", "chunk"))
    if embedding_text not in EMBEDDING_TEXT_MODES:
        raise EvalConfigError(
            f"retrieval.embedding_text 只能是 {sorted(EMBEDDING_TEXT_MODES)}"
        )
    business_filter = str(payload.get("business_filter", "hard"))
    if business_filter not in BUSINESS_FILTER_MODES:
        raise EvalConfigError(
            f"retrieval.business_filter 只能是 {sorted(BUSINESS_FILTER_MODES)}"
        )
    business_bonus = _number(
        payload.get("business_bonus", 0.02), "retrieval.business_bonus", minimum=0.0
    )
    if business_bonus > 1.0:
        raise EvalConfigError("retrieval.business_bonus 不能大于 1")
    rerank_alpha = _number(
        payload.get("rerank_alpha", 0.0), "retrieval.rerank_alpha", minimum=0.0
    )
    if rerank_alpha > 1.0:
        raise EvalConfigError("retrieval.rerank_alpha 不能大于 1")
    rerank_pool = _positive_int(
        payload.get("rerank_pool", 20), "retrieval.rerank_pool"
    )

    overrides_raw = payload.get("scenario_overrides", {})
    if not isinstance(overrides_raw, Mapping):
        raise EvalConfigError("retrieval.scenario_overrides 必须是对象")
    overrides: dict[str, ScenarioRetrievalOverride] = {}
    for scenario, entry in overrides_raw.items():
        if not isinstance(entry, Mapping):
            raise EvalConfigError(
                f"retrieval.scenario_overrides.{scenario} 必须是对象"
            )
        _check_unknown(
            entry,
            frozenset({"business_filter", "min_similarity", "top_k"}),
            f"retrieval.scenario_overrides.{scenario}",
        )
        override_filter = (
            str(entry["business_filter"])
            if entry.get("business_filter") is not None
            else None
        )
        if (
            override_filter is not None
            and override_filter not in BUSINESS_FILTER_MODES
        ):
            raise EvalConfigError(
                f"retrieval.scenario_overrides.{scenario}.business_filter 只能是 "
                f"{sorted(BUSINESS_FILTER_MODES)}"
            )
        override_similarity = (
            _number(
                entry["min_similarity"],
                f"retrieval.scenario_overrides.{scenario}.min_similarity",
            )
            if entry.get("min_similarity") is not None
            else None
        )
        if override_similarity is not None and not 0.0 <= override_similarity <= 1.0:
            raise EvalConfigError(
                f"retrieval.scenario_overrides.{scenario}.min_similarity 必须在 [0,1]"
            )
        overrides[str(scenario)] = ScenarioRetrievalOverride(
            business_filter=override_filter,
            min_similarity=override_similarity,
            top_k=(
                _positive_int(
                    entry["top_k"],
                    f"retrieval.scenario_overrides.{scenario}.top_k",
                )
                if entry.get("top_k") is not None
                else None
            ),
        )
    return RetrievalOptions(
        embedding_text=embedding_text,
        business_filter=business_filter,
        business_bonus=business_bonus,
        rerank_alpha=rerank_alpha,
        rerank_pool=rerank_pool,
        scenario_overrides=overrides,
    )


def load_retrieval_options(path: Path | str) -> RetrievalOptions:
    """从 EVAL_CONFIG 只读 ``retrieval`` 段；不做 split 守卫（供 dev 调参）。"""
    return parse_retrieval_options(_read_payload(Path(path)).get("retrieval"))


def load_eval_config(path: Path | str) -> EvalConfig:
    """读取并校验 EVAL_CONFIG；任何不合规都显式失败。"""
    payload = _read_payload(Path(path))
    _check_unknown(payload, _TOP_LEVEL_KEYS, "EVAL_CONFIG")

    mode = payload.get("mode")
    if mode not in MODES:
        raise EvalConfigError(f"mode 只能是 {sorted(MODES)}，收到 {mode!r}")
    split = require_frozen_test_split(str(payload.get("split", "")))

    model_section = payload.get("model")
    if not isinstance(model_section, Mapping):
        raise EvalConfigError("model 段必须是对象")
    _check_unknown(model_section, _MODEL_KEYS, "model")
    _require(model_section, ("provider", "model"), "model")

    embedding_section = payload.get("embedding")
    if not isinstance(embedding_section, Mapping):
        raise EvalConfigError("embedding 段必须是对象")
    _check_unknown(embedding_section, _EMBEDDING_KEYS, "embedding")
    _require(embedding_section, ("provider",), "embedding")
    embedding_provider = str(embedding_section["provider"])
    if embedding_provider not in EMBEDDING_PROVIDERS:
        raise EvalConfigError(
            f"embedding.provider 只能是 {sorted(EMBEDDING_PROVIDERS)}"
        )
    if mode == "replay" and embedding_provider != "replay_lexical":
        raise EvalConfigError(
            "replay 模式必须使用确定性 replay_lexical embedding；"
            "fastembed 会产生真实模型依赖，不能用于无成本回放"
        )

    dataset_id = str(payload.get("dataset_id", "")).strip()
    dataset_version = str(payload.get("dataset_version", "")).strip()
    if not dataset_id or not dataset_version:
        raise EvalConfigError("dataset_id 与 dataset_version 不能为空")
    artifact_root = Path(str(payload.get("artifact_root", "artifacts")))
    dataset_dir = Path(
        str(
            payload.get(
                "dataset_dir",
                (artifact_root / "data" / dataset_id / dataset_version).as_posix(),
            )
        )
    )

    seed = payload.get("seed", 42)
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise EvalConfigError("seed 必须是整数")
    top_k = _positive_int(payload.get("top_k", 5), "top_k")
    min_similarity = _number(payload.get("min_similarity", 0.55), "min_similarity")
    if not 0.0 <= min_similarity <= 1.0:
        raise EvalConfigError("min_similarity 必须在 [0, 1] 内")

    max_items = payload.get("max_items")
    if max_items is not None:
        max_items = _positive_int(max_items, "max_items")
    budget_cny = _number(payload.get("budget_cny", 0.0), "budget_cny", minimum=0.0)

    pricing_raw = payload.get("pricing", [])
    if not isinstance(pricing_raw, list):
        raise EvalConfigError("pricing 必须是数组")
    pricing: list[PriceEntry] = []
    for index, entry in enumerate(pricing_raw):
        if not isinstance(entry, Mapping):
            raise EvalConfigError(f"pricing[{index}] 必须是对象")
        _check_unknown(entry, _PRICE_KEYS, f"pricing[{index}]")
        _require(
            entry,
            ("provider", "model", "input_cny_per_mtok", "output_cny_per_mtok", "source", "as_of"),
            f"pricing[{index}]",
        )
        pricing.append(
            PriceEntry(
                provider=str(entry["provider"]),
                model=str(entry["model"]),
                input_cny_per_mtok=_number(
                    entry["input_cny_per_mtok"], f"pricing[{index}].input_cny_per_mtok", minimum=0.0
                ),
                output_cny_per_mtok=_number(
                    entry["output_cny_per_mtok"], f"pricing[{index}].output_cny_per_mtok", minimum=0.0
                ),
                source=str(entry["source"]),
                as_of=str(entry["as_of"]),
                note=str(entry["note"]) if entry.get("note") else None,
            )
        )

    tasks_raw = payload.get("tasks", list(TASKS))
    if not isinstance(tasks_raw, list) or not tasks_raw:
        raise EvalConfigError("tasks 必须是非空数组")
    tasks = tuple(str(task) for task in tasks_raw)
    if len(set(tasks)) != len(tasks) or any(task not in TASKS for task in tasks):
        raise EvalConfigError(f"tasks 只能取 {TASKS} 的子集且不得重复")

    knowledge_as_of_raw = payload.get("knowledge_as_of")
    if knowledge_as_of_raw is None:
        knowledge_as_of = CORPUS_AS_OF
    else:
        try:
            knowledge_as_of = datetime.fromisoformat(str(knowledge_as_of_raw))
        except ValueError as exc:
            raise EvalConfigError(f"knowledge_as_of 不是 ISO-8601：{exc}") from exc
        if knowledge_as_of.tzinfo is None:
            raise EvalConfigError("knowledge_as_of 必须带时区")

    notes = payload.get("notes")
    if notes is not None and not isinstance(notes, Mapping):
        raise EvalConfigError("notes 必须是对象")

    config = EvalConfig(
        mode=str(mode),
        split=split,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        dataset_dir=dataset_dir,
        artifact_root=artifact_root,
        seed=seed,
        top_k=top_k,
        min_similarity=min_similarity,
        max_items=max_items,
        budget_cny=budget_cny,
        model_provider=str(model_section["provider"]),
        model_name=str(model_section["model"]),
        embedding_provider=embedding_provider,
        pricing=tuple(pricing),
        scope_id=str(payload.get("scope_id", "demo-bank")),
        knowledge_as_of=knowledge_as_of,
        tasks=tasks,
        run_id=str(payload["run_id"]) if payload.get("run_id") else None,
        notes=dict(notes) if notes else None,
        retrieval=parse_retrieval_options(payload.get("retrieval")),
    )

    if config.is_live:
        problems: list[str] = []
        if config.max_items is None:
            problems.append("max_items（样本上限）")
        if config.budget_cny <= 0:
            problems.append("budget_cny（预算上限）")
        if problems:
            raise EvalConfigError("live 模式必须配置 " + "、".join(problems))
        config.price_for(config.model_provider, config.model_name)

    return config
