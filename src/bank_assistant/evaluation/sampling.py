"""小样本评测的分层抽样（T27，#28）。

live 模式必须限制样本量，但「按 item_id 取前 N 条」会把子集压成单一场景，
既覆盖不到困难样本，也会让无答案子集分母变成 0。这里按场景轮转、场景内
困难/正常交替，保证：

- 三场景尽量均衡；
- 有困难样本时优先纳入（困难/正常严格交替，避免全被困难样本占满）；
- 完全确定性（只依赖 item_id 排序），不引入随机数。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TypeVar

__all__ = ["stratified_sample"]

T = TypeVar("T")


def stratified_sample(
    items: Sequence[T],
    max_items: int | None,
    *,
    scenario_of: Callable[[T], str],
    is_hard: Callable[[T], bool],
) -> list[T]:
    """按场景轮转 + 场景内困难/正常交替抽取至多 ``max_items`` 条。"""
    if max_items is None:
        return list(items)
    if max_items <= 0:
        raise ValueError("max_items 必须为正整数")
    ordered_items = sorted(items, key=lambda item: getattr(item, "item_id", ""))
    if len(ordered_items) <= max_items:
        return ordered_items

    buckets: dict[str, list[T]] = {}
    for item in ordered_items:
        buckets.setdefault(scenario_of(item), []).append(item)

    ordered_buckets: dict[str, list[T]] = {}
    for scenario, group in buckets.items():
        hard = [item for item in group if is_hard(item)]
        normal = [item for item in group if not is_hard(item)]
        mixed: list[T] = []
        for index in range(max(len(hard), len(normal))):
            if index < len(hard):
                mixed.append(hard[index])
            if index < len(normal):
                mixed.append(normal[index])
        ordered_buckets[scenario] = mixed

    scenarios = sorted(ordered_buckets)
    selected: list[T] = []
    index = 0
    while len(selected) < max_items:
        added = False
        for scenario in scenarios:
            bucket = ordered_buckets[scenario]
            if index < len(bucket):
                selected.append(bucket[index])
                added = True
                if len(selected) == max_items:
                    break
        if not added:
            break
        index += 1
    return selected
