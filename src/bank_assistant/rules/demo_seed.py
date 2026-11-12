"""合成演示规则集的校验与导入（M4-01，#18）。

演示规则只用于本地演示与测试：``demo_only=true``，说明必须注明
「虚构演示制度」（GLOSSARY「演示制度」），来源只能是 ``demo.invalid`` 占位
地址或公开监管链接；不得出现任何机构的真实内部规则。

覆盖 M4-01/M4-02：至少 6 条规则，覆盖挂失/信用卡费用/投诉三场景与六个默认
类别（索取密码/验证码、无依据声称办理成功、无依据承诺减免费用、费用说明与
有效条款冲突、阻碍投诉、明显侮辱性表达），至少一条高风险规则；每条规则带
适用范围、证据要求、风险级别、正反例（契约 §4 证据 JSON）与豁免条件。

导入走真实生命周期（``create → submit → publish``），作者与审核人必须是两个
不同账号；本模块不创建任何用户。幂等规则：按 ``(scope_id, rule_code)`` 找
规则、按 ``version_no`` 找版本，已存在且内容哈希一致则跳过；内容不同则报错
（必须递增 ``version_no``，禁止覆盖已发布版本）。
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from bank_assistant.infra.models import RuleSet, RuleVersion
from bank_assistant.knowledge.lifecycle import (
    DEMO_BUSINESS_TYPES,
    DEMO_MARKER,
    validate_window,
)
from bank_assistant.rules.lifecycle import (
    DEFAULT_RULE_CATEGORIES,
    InvalidSeverity,
    RuleStatus,
    validate_severity,
)
from bank_assistant.rules.schema import (
    RuleApplicability,
    RuleCriteria,
    RuleExamples,
    RuleSchemaError,
    RuleSourceRef,
    demo_marker_present,
    validate_rule_examples,
    validate_source_refs,
)
# 注意：`bank_assistant.api.__init__` 会导入 routers，而 routers 又导入本包的
# service；先显式加载 api.errors，可避免直接 import 服务层时的循环导入。
import bank_assistant.api.errors  # noqa: E402, F401  isort:skip
from bank_assistant.rules.service import (  # noqa: E402
    NewRuleSet,
    RuleVersionInput,
    content_hash_for,
    create_rule_version,
    publish_rule_version,
    submit_rule_version,
)
from bank_assistant.security.identity import Identity

DEMO_RULES_DIR = Path(__file__).resolve().parents[3] / "data" / "demo" / "rules"
DEMO_MIN_RULES = 6
# 演示规则编号：DEMO-<类别缩写>-<两位序号>。
RULE_CODE_PATTERN = re.compile(r"^DEMO-[A-Z]{2,6}-\d{2}$")
REVIEW_REASON = "演示规则种子：作者提交，由另一审核人发布"

_REQUIRED_ENTRY_FIELDS: tuple[str, ...] = (
    "rule_code",
    "title",
    "category",
    "description",
    "demo_only",
    "business_type",
    "version_no",
    "severity",
    "valid_from",
    "applicability",
    "criteria",
    "exceptions",
    "source_refs",
    "examples",
)


class DemoRuleSeedError(ValueError):
    """演示规则数据或导入前置条件不合法；脚本应非零退出并说明原因。"""


@dataclass(frozen=True, slots=True)
class DemoRuleEntry:
    """一条已通过校验的演示规则种子。"""

    entry_id: str
    rule_code: str
    title: str
    category: str
    description: str
    demo_only: bool
    business_type: str
    version_no: int
    severity: str
    valid_from: datetime
    valid_to: datetime | None
    applicability: RuleApplicability
    criteria: RuleCriteria
    exceptions: tuple[str, ...]
    source_refs: tuple[RuleSourceRef, ...]
    examples: RuleExamples

    def as_version_input(self) -> RuleVersionInput:
        return RuleVersionInput(
            business_type=self.business_type,
            applicability=self.applicability,
            criteria=self.criteria,
            exceptions=self.exceptions,
            severity=self.severity,
            source_refs=self.source_refs,
            examples=self.examples,
            valid_from=self.valid_from,
            valid_to=self.valid_to,
        )

    def as_new_rule(self) -> NewRuleSet:
        return NewRuleSet(
            rule_code=self.rule_code,
            title=self.title,
            category=self.category,
            description=self.description,
            demo_only=self.demo_only,
        )


@dataclass(frozen=True, slots=True)
class DemoRuleSeedReport:
    """一次导入的幂等统计；脚本据此打印“新建/跳过”。"""

    total_rules: int
    created_rules: int
    published_versions: int
    skipped_versions: int


def _fail(message: str, *, source: str) -> DemoRuleSeedError:
    return DemoRuleSeedError(f"{source}: {message}")


def _parse_moment(value: object, *, field: str, source: str) -> datetime:
    if not isinstance(value, str):
        raise _fail(f"{field} 必须是 ISO 8601 字符串", source=source)
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _fail(f"{field} 不是合法 ISO 8601 时间：{value!r}", source=source) from exc
    if moment.tzinfo is None:
        raise _fail(f"{field} 必须带时区（契约 §1 统一 UTC）", source=source)
    return moment


def _parse_entry(raw: object, *, source: str, index: int) -> DemoRuleEntry:
    if not isinstance(raw, dict):
        raise _fail(f"第 {index} 条不是 JSON 对象", source=source)
    label = f"第 {index} 条（rule_code={raw.get('rule_code', '<missing>')}）"

    missing = [field for field in _REQUIRED_ENTRY_FIELDS if field not in raw]
    if missing:
        raise _fail(f"{label} 缺少字段：{', '.join(missing)}", source=source)

    rule_code = raw["rule_code"]
    if not isinstance(rule_code, str) or not RULE_CODE_PATTERN.match(rule_code):
        raise _fail(
            f"{label} 的 rule_code 必须形如 DEMO-XXX-01，实际 {rule_code!r}",
            source=source,
        )

    demo_only = raw["demo_only"]
    if demo_only is not True:
        raise _fail(f"{label} 是演示规则，demo_only 必须为 true", source=source)

    category = raw["category"]
    if category not in DEFAULT_RULE_CATEGORIES:
        raise _fail(
            f"{label} 的 category 必须是 M4-02 默认类别之一 "
            f"{list(DEFAULT_RULE_CATEGORIES)}，实际 {category!r}",
            source=source,
        )

    business_type = raw["business_type"]
    if business_type not in DEMO_BUSINESS_TYPES:
        raise _fail(
            f"{label} 的 business_type 必须是 {DEMO_BUSINESS_TYPES}，"
            f"实际 {business_type!r}",
            source=source,
        )

    title = raw["title"]
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise _fail(f"{label} 的 title 必须非空且不超过 200 字符", source=source)

    description = raw["description"]
    if not isinstance(description, str) or not description.strip():
        raise _fail(f"{label} 缺少非空 description", source=source)
    if not demo_marker_present(description):
        raise _fail(
            f"{label} 的 description 必须注明「{DEMO_MARKER}」，"
            "不得与真实制度混同",
            source=source,
        )

    version_no = raw["version_no"]
    if not isinstance(version_no, int) or version_no < 1:
        raise _fail(f"{label} 的 version_no 必须是正整数", source=source)

    severity = raw["severity"]
    try:
        validate_severity(severity)
    except InvalidSeverity as exc:
        raise _fail(f"{label} 的 {exc}", source=source) from exc

    valid_from = _parse_moment(raw["valid_from"], field="valid_from", source=source)
    valid_to = (
        _parse_moment(raw["valid_to"], field="valid_to", source=source)
        if raw.get("valid_to") is not None
        else None
    )
    try:
        validate_window(valid_from, valid_to)
    except ValueError as exc:
        raise _fail(f"{label} 的有效期非法：{exc}", source=source) from exc

    exceptions = raw["exceptions"]
    if not isinstance(exceptions, list) or not exceptions:
        raise _fail(f"{label} 必须至少声明一条豁免条件（exceptions）", source=source)
    if any(not isinstance(item, str) or not item.strip() for item in exceptions):
        raise _fail(f"{label} 的 exceptions 条目必须是非空字符串", source=source)

    try:
        applicability = RuleApplicability.model_validate(raw["applicability"])
        criteria = RuleCriteria.model_validate(raw["criteria"])
        source_refs = tuple(
            RuleSourceRef.model_validate(item) for item in raw["source_refs"]
        )
        examples = RuleExamples.model_validate(raw["examples"])
    except ValidationError as exc:
        raise _fail(f"{label} 内容不合法：{exc}", source=source) from exc

    if business_type not in applicability.business_types:
        raise _fail(
            f"{label} 的 applicability.business_types 必须包含 {business_type}",
            source=source,
        )

    try:
        validate_source_refs(list(source_refs), demo_only=True)
        validate_rule_examples(
            examples,
            rule_code=rule_code,
            version_no=version_no,
            severity=severity,
        )
    except RuleSchemaError as exc:
        raise _fail(f"{label} {exc}", source=source) from exc

    entry_id = raw.get("id")
    if not isinstance(entry_id, str) or not entry_id.strip():
        entry_id = rule_code

    return DemoRuleEntry(
        entry_id=entry_id,
        rule_code=rule_code,
        title=title,
        category=category,
        description=description,
        demo_only=True,
        business_type=business_type,
        version_no=version_no,
        severity=severity,
        valid_from=valid_from,
        valid_to=valid_to,
        applicability=applicability,
        criteria=criteria,
        exceptions=tuple(exceptions),
        source_refs=source_refs,
        examples=examples,
    )


def _validate_corpus(entries: Sequence[DemoRuleEntry], *, source: str) -> None:
    """整份演示规则集必须覆盖三场景与六个默认类别，且至少一条高风险。"""
    if len(entries) < DEMO_MIN_RULES:
        raise _fail(
            f"演示规则集至少需要 {DEMO_MIN_RULES} 条规则（M4-01），"
            f"实际 {len(entries)} 条",
            source=source,
        )

    scenarios = {entry.business_type for entry in entries}
    missing_scenarios = set(DEMO_BUSINESS_TYPES) - scenarios
    if missing_scenarios:
        raise _fail(
            f"演示规则集必须覆盖全部业务场景 {DEMO_BUSINESS_TYPES}，"
            f"缺少场景 {sorted(missing_scenarios)}",
            source=source,
        )

    categories = {entry.category for entry in entries}
    missing_categories = set(DEFAULT_RULE_CATEGORIES) - categories
    if missing_categories:
        raise _fail(
            f"演示规则集必须覆盖 M4-02 的全部 category {list(DEFAULT_RULE_CATEGORIES)}，"
            f"缺少 {sorted(missing_categories)}",
            source=source,
        )
    unexpected = categories - set(DEFAULT_RULE_CATEGORIES)
    if unexpected:
        raise _fail(
            f"演示规则集的 category 必须是 M4-02 默认类别，非预期 {sorted(unexpected)}",
            source=source,
        )

    if not any(entry.severity == "high" for entry in entries):
        raise _fail("演示规则集必须至少包含一条高风险规则", source=source)


def load_demo_rules(
    directory: Path | str = DEMO_RULES_DIR,
) -> tuple[DemoRuleEntry, ...]:
    """读取目录下全部 ``*.json`` 规则种子并校验；任何一条不合法都拒绝整份。"""
    root = Path(directory)
    files = sorted(root.glob("*.json"))
    if not files:
        raise DemoRuleSeedError(f"没有找到演示规则文件：{root}")

    entries: list[DemoRuleEntry] = []
    seen_codes: set[str] = set()
    for file_path in files:
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DemoRuleSeedError(f"{file_path.name}: 无法解析 JSON（{exc}）") from exc
        raw_entries = payload.get("entries") if isinstance(payload, dict) else payload
        if not isinstance(raw_entries, list):
            raise DemoRuleSeedError(f"{file_path.name}: 缺少 entries 列表")

        for index, raw in enumerate(raw_entries, start=1):
            entry = _parse_entry(raw, source=file_path.name, index=index)
            if entry.rule_code in seen_codes:
                raise _fail(
                    f"规则编码 {entry.rule_code!r} 重复", source=file_path.name
                )
            seen_codes.add(entry.rule_code)
            entries.append(entry)

    _validate_corpus(entries, source="演示规则集")
    return tuple(entries)


def _validate_accounts(
    *,
    author: Identity,
    reviewer: Identity,
    scope_id: str,
) -> None:
    if author.scope_id != scope_id or reviewer.scope_id != scope_id:
        raise DemoRuleSeedError(
            f"演示账号必须属于 scope {scope_id!r}："
            f"作者 {author.scope_id!r}，审核人 {reviewer.scope_id!r}"
        )
    if author.user_id == reviewer.user_id:
        raise DemoRuleSeedError("作者与审核人必须是两个不同账号（发布者与作者分离）")
    if author.role not in {"agent", "admin"}:
        raise DemoRuleSeedError(f"演示作者角色必须为 agent/admin，实际 {author.role!r}")
    if reviewer.role not in {"reviewer", "admin"}:
        raise DemoRuleSeedError(
            f"演示审核人角色必须为 reviewer/admin，实际 {reviewer.role!r}"
        )


def _find_rule_set(
    session: Session, *, scope_id: str, rule_code: str
) -> RuleSet | None:
    return session.scalar(
        select(RuleSet).where(
            RuleSet.scope_id == scope_id,
            RuleSet.rule_code == rule_code,
        )
    )


def _find_version(
    session: Session, *, rule_id, version_no: int
) -> RuleVersion | None:
    return session.scalar(
        select(RuleVersion).where(
            RuleVersion.rule_id == rule_id,
            RuleVersion.version_no == version_no,
        )
    )


def import_demo_rules(
    session: Session,
    entries: Sequence[DemoRuleEntry],
    *,
    author: Identity,
    reviewer: Identity,
    scope_id: str,
) -> DemoRuleSeedReport:
    """幂等导入演示规则：作者提交、另一审核人发布；已存在且一致则跳过。"""
    _validate_accounts(author=author, reviewer=reviewer, scope_id=scope_id)

    created_rules = 0
    published_versions = 0
    skipped_versions = 0

    for entry in entries:
        expected_hash = content_hash_for(entry.as_version_input(), demo_only=True)
        rule_set = _find_rule_set(
            session, scope_id=scope_id, rule_code=entry.rule_code
        )
        if rule_set is not None:
            if (
                rule_set.demo_only != entry.demo_only
                or rule_set.title != entry.title
                or rule_set.category != entry.category
                or rule_set.description != entry.description
            ):
                raise DemoRuleSeedError(
                    f"规则 {entry.rule_code} 已存在但身份元数据不一致，"
                    "演示种子不允许静默改写，请人工核对"
                )
            existing = _find_version(
                session, rule_id=rule_set.rule_id, version_no=entry.version_no
            )
            if existing is not None:
                if existing.content_hash != expected_hash:
                    raise DemoRuleSeedError(
                        f"{entry.rule_code} v{entry.version_no} 已存在且内容不同；"
                        "请递增 version_no，不能覆盖已发布版本"
                    )
                if existing.status != RuleStatus.PUBLISHED.value:
                    raise DemoRuleSeedError(
                        f"{entry.rule_code} v{entry.version_no} 已存在但状态为 "
                        f"{existing.status}，演示规则必须最终处于已发布状态"
                    )
                skipped_versions += 1
                continue

        if rule_set is None:
            rule_set, version = create_rule_version(
                session,
                author,
                rule=entry.as_new_rule(),
                version=entry.as_version_input(),
            )
            created_rules += 1
        else:
            rule_set, version = create_rule_version(
                session,
                author,
                rule_id=rule_set.rule_id,
                version=entry.as_version_input(),
            )
        if version.version_no != entry.version_no:
            raise DemoRuleSeedError(
                f"{entry.rule_code} 期望 version_no={entry.version_no}，"
                f"实际写入 {version.version_no}；种子版本号必须连续"
            )

        submit_rule_version(
            session, author, version.rule_version_id, expected_revision=version.revision
        )
        published = publish_rule_version(
            session,
            reviewer,
            version.rule_version_id,
            expected_revision=version.revision,
            review_reason=REVIEW_REASON,
        )
        if published.outcome != "published" or published.superseded:
            raise DemoRuleSeedError(
                f"{entry.rule_code} 发布结果异常：outcome={published.outcome}，"
                f"退役版本={len(published.superseded)}；演示种子不应退役已有版本"
            )
        published_versions += 1

    return DemoRuleSeedReport(
        total_rules=len(entries),
        created_rules=created_rules,
        published_versions=published_versions,
        skipped_versions=skipped_versions,
    )


__all__ = [
    "DEMO_RULES_DIR",
    "DEMO_MIN_RULES",
    "REVIEW_REASON",
    "RULE_CODE_PATTERN",
    "DemoRuleEntry",
    "DemoRuleSeedError",
    "DemoRuleSeedReport",
    "import_demo_rules",
    "load_demo_rules",
]
