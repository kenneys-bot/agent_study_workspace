"""合成演示会话的校验、幂等导入与质检种子状态（T25，M5-04）。

三个可验收业务故事（计划书 §3.3）各准备两段会话：``normal`` 与 ``risk``。
全部内容为合成数据，不包含任何真实客户、账号、卡号或机构内部资料：

- ``S1`` 挂失咨询：正常指引 / 索取验证码并声称已办理成功；
- ``S2`` 信用卡费用：按条款说明并追问卡种 / 无依据承诺减免；
- ``S3`` 投诉受理：正常登记渠道 / 阻碍投诉。

导入走真实的会话持久化服务（``create_conversation``），原文不可变；幂等键是
``(scope_id, source_ref)``，内容一致则跳过，内容变化则显式失败（要求人工新建
会话，不静默覆盖）。``create_inspection=true`` 的会话在导入后通过**显式回放**
跑一次真实质检 worker，并为 ``demo-reviewer`` 创建待复核任务；回放不会调用
任何云模型，也不把回放结果当成真实模型质量证据。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from bank_assistant.assistants.quality_inspector.conversation_parser import (
    ConversationParseError,
    ConversationParser,
)
from bank_assistant.demo.replay import DemoReplayResponder
from bank_assistant.infra.db import session_scope
from bank_assistant.infra.models import (
    Conversation,
    InspectionRun,
    ReviewCase,
    User,
)
from bank_assistant.infra.task_queue import TaskQueue
from bank_assistant.core.model_gateway import ModelGateway
from bank_assistant.inspections.service import create_inspection
from bank_assistant.knowledge.lifecycle import DEMO_BUSINESS_TYPES
from bank_assistant.reviews.service import create_review
from bank_assistant.security.identity import Identity
from bank_assistant.services.conversations import (
    ConversationIntegrityError,
    create_conversation,
)
from bank_assistant.worker import Worker

DEMO_CONVERSATION_DIR = (
    Path(__file__).resolve().parents[3] / "data" / "demo" / "conversations"
)

DEMO_REVIEWER_USERNAME = "demo-reviewer"
DEMO_AUTHOR_USERNAME = "demo-author"

_SOURCE_PREFIX = "demo://conversations/"
_STORY_SCENARIOS = {
    "S1": {"normal", "risk"},
    "S2": {"normal", "risk"},
    "S3": {"normal", "risk"},
}


class DemoConversationError(ValueError):
    """演示会话数据或导入前置条件不合法；脚本必须非零退出。"""


@dataclass(frozen=True, slots=True)
class DemoConversation:
    """一条通过校验的合成演示会话。"""

    entry_id: str
    story: str
    story_title: str
    scenario: str
    title: str
    business_type: str
    description: str
    content: str
    create_inspection: bool

    @property
    def source_ref(self) -> str:
        return f"{_SOURCE_PREFIX}{self.entry_id}"

    @property
    def content_hash(self) -> str:
        import hashlib

        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class DemoConversationSeedReport:
    """一次导入的幂等统计与质检种子结果。"""

    total_entries: int
    created_conversations: int
    skipped_conversations: int
    created_inspections: int
    created_reviews: int
    review_ids: tuple[UUID, ...]


def _fail(message: str, *, source: str) -> DemoConversationError:
    return DemoConversationError(f"{source}: {message}")


def _parse_entry(raw: object, *, source: str, index: int) -> DemoConversation:
    if not isinstance(raw, dict):
        raise _fail(f"第 {index} 条不是 JSON 对象", source=source)
    label = f"第 {index} 条（id={raw.get('id', '<missing>')}）"
    required = (
        "id",
        "story",
        "story_title",
        "scenario",
        "title",
        "business_type",
        "description",
        "content",
    )
    missing = [key for key in required if key not in raw]
    if missing:
        raise _fail(f"{label} 缺少字段：{', '.join(missing)}", source=source)

    entry_id = raw["id"]
    if not isinstance(entry_id, str) or not entry_id.strip():
        raise _fail(f"{label} 的 id 必须是非空字符串", source=source)
    story = raw["story"]
    scenario = raw["scenario"]
    if story not in _STORY_SCENARIOS:
        raise _fail(f"{label} 的 story 必须是 S1/S2/S3", source=source)
    if scenario not in _STORY_SCENARIOS[story]:
        raise _fail(f"{label} 的 scenario 必须是 normal/risk", source=source)
    business_type = raw["business_type"]
    if business_type not in DEMO_BUSINESS_TYPES:
        raise _fail(
            f"{label} 的 business_type 必须是 {DEMO_BUSINESS_TYPES}，"
            f"实际 {business_type!r}",
            source=source,
        )
    content = raw["content"]
    if not isinstance(content, str) or len(content.strip()) < 20:
        raise _fail(f"{label} 的 content 必须是非空合成会话（至少 20 字符）", source=source)
    if raw.get("data_classification", "synthetic_demo") != "synthetic_demo":
        raise _fail(f"{label} 只允许 data_classification=synthetic_demo", source=source)
    if any(character.isdigit() for character in content):
        # 演示会话刻意不写手机号、卡号、身份证号等任何号码；出现数字就拒绝，
        # 避免把“看起来像真实数据”的内容带进仓库或演示库。
        raise _fail(f"{label} 的会话正文不得包含数字（避免任何真实号码形态）", source=source)
    description = raw["description"]
    if not isinstance(description, str) or len(description.strip()) < 10:
        raise _fail(f"{label} 的 description 必须说明场景", source=source)
    create_inspection = raw.get("create_inspection", False)
    if not isinstance(create_inspection, bool):
        raise _fail(f"{label} 的 create_inspection 必须是布尔值", source=source)

    return DemoConversation(
        entry_id=entry_id.strip(),
        story=story,
        story_title=str(raw["story_title"]),
        scenario=scenario,
        title=str(raw["title"]),
        business_type=business_type,
        description=description,
        content=content,
        create_inspection=create_inspection,
    )


def load_demo_conversations(
    directory: Path | None = None,
) -> list[DemoConversation]:
    """读取并校验全部演示会话；缺故事/重复 id 都显式失败。"""
    root = directory or DEMO_CONVERSATION_DIR
    if not root.is_dir():
        raise DemoConversationError(f"演示会话目录不存在：{root}")
    entries: list[DemoConversation] = []
    for index, path in enumerate(sorted(root.glob("*.json")), start=1):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DemoConversationError(f"{path.name}: 不是合法 JSON：{exc}") from exc
        entries.append(_parse_entry(raw, source=path.name, index=index))

    if not entries:
        raise DemoConversationError(f"演示会话目录为空：{root}")
    ids = [entry.entry_id for entry in entries]
    if len(set(ids)) != len(ids):
        raise DemoConversationError("演示会话 id 必须唯一")

    seen: set[tuple[str, str]] = set()
    for entry in entries:
        key = (entry.story, entry.scenario)
        if key in seen:
            raise DemoConversationError(
                f"每个业务故事只能有一个 {entry.scenario} 会话（重复：{entry.story}）"
            )
        seen.add(key)
    expected = {
        (story, scenario)
        for story, scenarios in _STORY_SCENARIOS.items()
        for scenario in scenarios
    }
    missing = sorted(expected - seen)
    if missing:
        labels = "、".join(f"{story}/{scenario}" for story, scenario in missing)
        raise DemoConversationError(f"缺少演示会话：{labels}")
    return entries


def _identity(username: str, session: Session) -> Identity | None:
    user = session.scalar(select(User).where(User.username == username))
    if user is None or not user.active:
        return None
    return Identity(
        user_id=user.user_id,
        username=user.username,
        role=user.role,
        scope_id=user.scope_id,
    )


def _find_conversation(
    session: Session, *, scope_id: str, source_ref: str
) -> Conversation | None:
    matches = session.scalars(
        select(Conversation).where(
            Conversation.scope_id == scope_id,
            Conversation.source_ref == source_ref,
        )
    ).all()
    if len(matches) > 1:
        raise DemoConversationError(
            f"同一 source_ref 存在多条会话（{source_ref}），请人工核对"
        )
    return matches[0] if matches else None


def _latest_inspection(
    session: Session, conversation_id: UUID
) -> InspectionRun | None:
    return session.scalar(
        select(InspectionRun)
        .where(InspectionRun.conversation_id == conversation_id)
        .order_by(InspectionRun.created_at.desc(), InspectionRun.inspection_id.desc())
        .limit(1)
    )


def _review_for_inspection(
    session: Session, inspection_id: UUID
) -> ReviewCase | None:
    return session.scalar(
        select(ReviewCase)
        .where(ReviewCase.inspection_id == inspection_id)
        .order_by(ReviewCase.created_at.desc())
        .limit(1)
    )


def import_demo_conversations(
    session: Session,
    entries: Sequence[DemoConversation],
    *,
    author: Identity,
    reviewer: Identity,
    scope_id: str,
    trace_id: str = "demo-seed-conversations",
    run_inspections: bool = True,
) -> DemoConversationSeedReport:
    """幂等导入会话，并（可选）用显式回放生成待复核质检任务。"""
    parser = ConversationParser()
    created_conversations = 0
    skipped_conversations = 0
    created_inspections = 0
    created_reviews = 0
    review_ids: list[UUID] = []

    for entry in entries:
        existing = _find_conversation(
            session, scope_id=scope_id, source_ref=entry.source_ref
        )
        if existing is not None:
            if existing.content_hash != entry.content_hash:
                raise DemoConversationError(
                    f"演示会话 {entry.entry_id} 已存在但内容哈希不同；"
                    "会话原文不可变，请新建 entry id，不能静默覆盖"
                )
            conversation_id = existing.conversation_id
            skipped_conversations += 1
        else:
            try:
                parsed = parser.parse_conversation(entry.content, "text")
            except ConversationParseError as exc:
                raise DemoConversationError(
                    f"演示会话 {entry.entry_id} 无法解析：{exc.message}"
                ) from exc
            try:
                view = create_conversation(
                    session,
                    identity=author,
                    parsed=parsed,
                    source_type="demo_seed",
                    source_ref=entry.source_ref,
                    data_classification="synthetic_demo",
                    trace_id=f"{trace_id}:{entry.entry_id}",
                )
            except ConversationIntegrityError as exc:
                raise DemoConversationError(
                    f"演示会话 {entry.entry_id} 轮次非法：{exc}"
                ) from exc
            conversation_id = view.conversation.conversation_id
            created_conversations += 1

        if not run_inspections or not entry.create_inspection:
            continue

        inspection = _latest_inspection(session, conversation_id)
        # 已有终态结果时直接复用；失败/取消或缺结果才新建，避免重跑产生重复任务。
        if inspection is None or inspection.status in {"failed", "cancelled"}:
            inspection, _ = create_inspection(
                session,
                author,
                conversation_id=conversation_id,
                business_type=entry.business_type,
                idempotency_key=f"demo-seed:{entry.entry_id}",
            )
            created_inspections += 1
        review = _review_for_inspection(session, inspection.inspection_id)
        if review is not None:
            review_ids.append(review.review_id)
            continue
        # create_review 只允许 succeeded；若刚创建 inspection，先由回放 worker 执行。
        if inspection.status != "succeeded":
            session.commit()
            _run_replay_worker(scope_id=scope_id)
            session.expire_all()
            inspection = session.get(InspectionRun, inspection.inspection_id)
            if inspection is None or inspection.status != "succeeded":
                raise DemoConversationError(
                    f"演示质检 {inspection.inspection_id if inspection else '<missing>'} "
                    f"未成功（status={inspection.status if inspection else 'missing'}），"
                    "不会为失败质检创建复核"
                )
        review = create_review(
            session,
            author,
            inspection_id=inspection.inspection_id,
            assignee_id=reviewer.user_id,
            trace_id=f"{trace_id}:review:{entry.entry_id}",
        )
        created_reviews += 1
        review_ids.append(review.review_id)

    return DemoConversationSeedReport(
        total_entries=len(entries),
        created_conversations=created_conversations,
        skipped_conversations=skipped_conversations,
        created_inspections=created_inspections,
        created_reviews=created_reviews,
        review_ids=tuple(review_ids),
    )


def _run_replay_worker(*, scope_id: str) -> None:
    """用显式回放跑真实 worker，只处理刚创建的演示质检任务。"""
    gateway = ModelGateway(replay_responder=DemoReplayResponder())
    worker = Worker(
        gateway=gateway,
        queue=TaskQueue(),
        worker_id=f"demo-seed-{scope_id}",
    )
    worker.drain()


def write_seed_state(path: Path, entries: Sequence[DemoConversation], *, scope_id: str) -> None:
    """写出演示状态清单（已 gitignore），供演示脚本与浏览器 e2e 定位任务。

    只记录 ID、标题与计数，不复制会话正文；正文仍以数据库与 JSON 种子为准。
    """
    with session_scope() as session:
        conversations = {
            row.source_ref: row.conversation_id
            for row in session.scalars(
                select(Conversation).where(Conversation.scope_id == scope_id)
            ).all()
            if row.source_ref is not None
        }
        users = {
            user.username: str(user.user_id)
            for user in session.scalars(
                select(User).where(
                    User.username.in_([DEMO_AUTHOR_USERNAME, DEMO_REVIEWER_USERNAME])
                )
            ).all()
        }
        payload: dict[str, Any] = {
            "generated_at": datetime.now(UTC).isoformat(),
            "scope_id": scope_id,
            "users": users,
            "conversations": {},
            "reviews": {},
        }
        for entry in entries:
            conversation_id = conversations.get(entry.source_ref)
            if conversation_id is None:
                continue
            item: dict[str, Any] = {
                "conversation_id": str(conversation_id),
                "story": entry.story,
                "scenario": entry.scenario,
                "business_type": entry.business_type,
                "source_ref": entry.source_ref,
            }
            inspection = _latest_inspection(session, conversation_id)
            if inspection is not None:
                item["inspection_id"] = str(inspection.inspection_id)
                item["inspection_status"] = inspection.status
                review = _review_for_inspection(session, inspection.inspection_id)
                if review is not None:
                    item["review_id"] = str(review.review_id)
                    item["review_status"] = review.status
                    item["review_revision"] = review.revision
                    payload["reviews"][entry.entry_id] = {
                        "review_id": str(review.review_id),
                        "inspection_id": str(inspection.inspection_id),
                        "status": review.status,
                        "revision": review.revision,
                    }
            payload["conversations"][entry.entry_id] = item

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


__all__ = [
    "DEMO_AUTHOR_USERNAME",
    "DEMO_CONVERSATION_DIR",
    "DEMO_REVIEWER_USERNAME",
    "DemoConversation",
    "DemoConversationError",
    "DemoConversationSeedReport",
    "import_demo_conversations",
    "load_demo_conversations",
    "write_seed_state",
]
