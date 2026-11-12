"""live 小样本分层抽样（T27，#28）的离线单测。"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from bank_assistant.evaluation.sampling import stratified_sample


@dataclass(frozen=True)
class _Item:
    item_id: str
    scenario: str
    hard_categories: tuple[str, ...] = ()


def _items() -> list[_Item]:
    items: list[_Item] = []
    for scenario in ("a", "b", "c"):
        for index in range(10):
            hard = ("no_answer",) if index % 3 == 0 else ()
            items.append(_Item(f"{scenario}-{index:02d}", scenario, hard))
    return items


def test_sample_is_balanced_across_scenarios_and_includes_hard_items() -> None:
    sampled = stratified_sample(
        _items(),
        6,
        scenario_of=lambda item: item.scenario,
        is_hard=lambda item: bool(item.hard_categories),
    )
    assert len(sampled) == 6
    assert {item.scenario for item in sampled} == {"a", "b", "c"}
    assert all(any(item.hard_categories for item in sampled if item.scenario == s) for s in "abc")


def test_sample_is_deterministic_and_respects_cap() -> None:
    first = stratified_sample(
        _items(),
        7,
        scenario_of=lambda item: item.scenario,
        is_hard=lambda item: bool(item.hard_categories),
    )
    shuffled = list(reversed(_items()))
    second = stratified_sample(
        shuffled,
        7,
        scenario_of=lambda item: item.scenario,
        is_hard=lambda item: bool(item.hard_categories),
    )
    assert [item.item_id for item in first] == [item.item_id for item in second]
    assert len(first) == 7


def test_sample_returns_all_when_cap_covers_dataset() -> None:
    sampled = stratified_sample(
        _items(),
        30,
        scenario_of=lambda item: item.scenario,
        is_hard=lambda item: bool(item.hard_categories),
    )
    assert len(sampled) == 30
    assert stratified_sample(
        _items(),
        None,
        scenario_of=lambda item: item.scenario,
        is_hard=lambda item: bool(item.hard_categories),
    ) == _items()


def test_zero_cap_is_rejected() -> None:
    with pytest.raises(ValueError):
        stratified_sample(
            _items(),
            0,
            scenario_of=lambda item: item.scenario,
            is_hard=lambda item: bool(item.hard_categories),
        )
